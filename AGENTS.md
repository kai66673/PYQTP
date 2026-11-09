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

- `uv run ruff check <path>` — configs live per package (`[tool.ruff]` in each pyqtp package's
  pyproject: `include` is only `pyqtp/**/*.py`, so `examples/` and `tests/` are not linted;
  `qtmonaco` has no ruff config). **`fix = true` is set, so a plain `ruff check` rewrites files**;
  use `--no-fix` to only report. Baseline failure is expected: 13 errors repo-wide (9 in the pyqtp
  packages, 3 in `qtmonaco` via ruff defaults, 1 in root `conftest.py`) — the root pyproject has no
  `[tool.ruff]`, so `ruff check` from the repo root lints root-level files with ruff DEFAULT config.
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
   + stage-9.1 project tree panel (test_project_tree.py, offscreen GUI без LSP/WebEngine, без маркеров)
   + stage-9.2 quick open (test_quick_open.py: scorer/scan unit без GUI + live offscreen диалог)
   + stage-9.3 project search (test_project_search.py: argv/парсер юниты без GUI и без бинаря rg + live offscreen с живым rg, skipif без rg)
   + stage-9.4 workspace/symbol (test_workspace_symbol.py: юниты разбора ответа/гейтов/`sample_file_for` без GUI + live hub с basedpyright + live offscreen диалог)
   + stage-9.5 project fileops (test_project_fileops.py: юниты fileops/file_rename без GUI + offscreen меню/обработчики с фейковым хабом + live basedpyright end-to-end rename)
   + stage-9.6 project session (test_project_session.py: юниты project_session без GUI + offscreen вкладки/сессия с фейковой вьюхой без WebEngine)
   + stage-9.6B project settings (test_project_settings.py: юниты project_settings/coerce/merge без GUI + offscreen двухслойность темы/редактора/LSP с фейковым edit_widget-рекордером без WebEngine)
   + stage-9.6B2 settings scope selector (test_settings_scope.py: offscreen реестр областей/видимость строки «Область»/откат области при закрытии проекта + scope-aware apply/reset страниц «Редактор»/«Внешний вид»/«LSP» + пакетный project_settings_update, без WebEngine)
   + stage-9.7 bookmarks (test_bookmarks.py: юниты чистого bookmarks.py без GUI + offscreen контрол/панель с фейк-редактором (gutter-сигнал, зеркало каретки, next/prev, панель) без WebEngine + 4 webengine-пробы: DOM-глифы `.pyqtp-bookmark-glyph`, смена модели, `_current_cursor`-зеркало, `_toggle_bookmark_request`→сигнал)). Markers `live`
   + stage-10.1 problems (test_lsp_hub_store.py: юниты чистого problems.py + python-stора диагностик LspHub без процессов + 3 live: детерминированный фейковый LSP-сервер `python <скрипт>` через реестр с ws-клиентом и publishDiagnostics, плюс live basedpyright skipif; test_problems_panel.py: offscreen панель «Проблемы» с фейк-документами из харнеса test_bookmarks — переиспользование импортом, для этого `pyqtp-ide/tests` в extraPaths pyrightconfig)
  (real LSP server processes) and
  `webengine` (real QWebEngineView/Monaco) are deselected by default via `addopts` → 195 passed / 41
  deselected; run them with `uv run pytest -m 'live or webengine'` (~3½ min, they work offscreen) or
  all 236 in one
  process with `uv run pytest -m ''` (~3½ min, full run keeps passing despite `os._exit` in
  `pytest_sessionfinish` eating the summary line — count the dots; NB the stage81
  document-symbols/document-highlight webengine tests are cold-start flaky under full-suite
  load — basedpyright answers `[]`/extra word-highlights while still indexing; re-run or run
  them alone and they pass). Test-suite gotchas:
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
    оверрайдов `showEvent/closeEvent` (у `eventFilter` стаб — `arg__1`/
    `arg__2`) должны совпадать со стабами PySide6 (`arg__1`), а `showEvent`
    и `eventFilter` требуют `# noqa: N802` — в pep8-naming белом списке есть
    `closeEvent`/`keyPressEvent`, но нет `showEvent`/`eventFilter`.
- Панель проекта (этап 9.1, план — `docs/IDE_ROADMAP.md`): контрол
  `ProjectControl` (`pyqtp-ide/pyqtp/controls/project_control/project_control.py`,
  key = qualname, регистрируется в `IdeMainWindow.init_controls` после
  `DocumentManager`) хранит корень проекта (QSettings ключ
  `ProjectControl/root_path`, при закрытии снимается), даёт console-API
  `project_root/project_open/project_close` и пункты меню «Файл» (priority):
  «Открыть папку...» (Ctrl+Shift+O, 4) / «Быстрый переход по файлам...»
  (Ctrl+P, 7) / «Переход к символу проекта...» (Ctrl+T, 8) / «Поиск по
  проекту...» (Ctrl+Shift+F, 9) / «Закрыть папку» (10).
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
   Быстрый переход (этап 9.2): `quick_open.py` — чистый (без GUI) DP
   fuzzy-скорер `fuzzy_score` + синхронный скан `iter_project_files` (тот же
   prune, что у дерева); `QuickOpenDialog` — QLineEdit+QListWidget, выбор →
   `file_selected(абсолютный путь)` → `open_document`. Скан крутится в
   `threading.Thread` (daemon) и приходит queued-сигналом через
   `_ScanSignals(QObject)`; результат отбрасывается по поколению
   `_scan_gen` (инкремент при смене/закрытии корня и в `teardown_views`).
   Экшен `action_quick_open` (Ctrl+P, «Файл» priority=7) активен только с
   открытым проектом; console-API `quick_open_show`. Нюанс скоринга: бонус
   границы даётся только ПЕРВОМУ символу query (иначе «p после точки» в
   `deep.py` обгонял бы цепочку «подряд» в `dp.py`), итог считается по
   `best_any[-1]` — совпасть должна вся query, а не префикс.
   Поиск по проекту (этап 9.3): чистый `project_search.py` (argv
   `build_rg_args` + инкрементный парсер `rg --json`; байтовые смещения rg
   конвертируются в UTF-16-колонки Monaco той же схемой, что `workspace_edit.py`
   — кириллица/эмодзи!) + вьюха `ProjectSearchView` (key
   `ProjectControl_search_view`, `widget_area='bottom'`), которая ДЕРЖИТ
   `QProcess` с rg у себя (паттерн 9.1 — источник в вьюхе): cwd=корень,
   аргумент `.` (пути в выводе с `./`), скрытые/gitignored rg не отдаёт;
   перезапуск/смена корня/teardown — `stop_search()` (gen-гейт `_search_gen`
   отбрасывает чужие сигналы процесса), срезка `MAX_MATCHES=2000` с kill,
   exit≥2 = «Ошибка: ...» (битый regex идёт в stderr даже с `--no-messages`),
   FailedToStart = «rg не найден». Клик по совпадению → `match_activated` →
   `doc_ctrl.open_definition_uri` (тот же путь, что LSP-переходы этапа 6);
   экшен `action_project_search` активируется вместе с quick-open в
   `_start_scan`, гаснет в `_on_root_for_scan(None)`.
   Символы проекта (этап 9.4, остаток этапа 8): чистый
   `controls/lsp_control/workspace_symbol.py` — разбор ответа `workspace/symbol`
   в обеих формах спеки (`SymbolInformation` и `WorkspaceSymbol` 3.17 +
   устаревшие верхнеуровневые `range`/`selectionRange`, fallback без range —
   (1,1)), координаты Monaco 1-based (тот же payload, что `open_definition_uri`),
   русские тексты kind'ов — + диалог `WorkspaceSymbolDialog` (в
   `project_control/`, в стиле quick open, дебаунс 400 мс, queued-мост
   `_SymbolSignals` из потока event-loop хаба, свой гейт поколения `_req_gen`
   + `drop_pending()` в смене/закрытии проекта и teardown); экшен
   `action_workspace_symbols` (Ctrl+T) активируется/гаснет вместе с
   quick-open/поиском, console-API `workspace_symbols_show`, выбор →
   `doc_ctrl.open_definition_uri` (тот же путь, что LSP-переходы).
   Файловые операции «Проводника» (этап 9.5): `ProjectTreeView` строит только
   контекстное меню QMenu (позиция → путь через `indexAt`, тексты пунктов:
   «Открыть»/«Новый файл»/«Новая папка»/«Переименовать»/«Удалить в корзину»;
   пустая область — только создание в корень) и эмитит сигналы намерений
   `create_file_requested/create_folder_requested/rename_requested/
   delete_requested`; диалоги (QInputDialog/QMessageBox), диск и LSP-обход —
   на `ProjectControl` поверх чистых хелперов `project_control/project_fileops.py`
   (валидация имени/`create_entry`/`renamed_path`/`move_to_trash` —
   `QFile.moveToTrash()`, при недоступной корзине отказ с предупреждением,
   никогда не удаляем безвозвратно молча; квирка: рантайм PySide6 6.6 отдаёт
   `bool`, стабы объявляют пару `(bool, путь_в_корзине)` — нормализованы обе
   формы). Rename-flow: валидация -> при открытом документе с правками отказ,
   чистая вкладка закрывается заранее (`DocumentManager.has_document/
   document_is_modified/close_document/documents_under` + общий хелпер
   `_force_close_doc_dock`) -> `lsp_hub.will_rename_files_async(root, old_uri,
   new_uri, cb)` (чистый разбор/мердж — `lsp_control/file_rename.py`; поддержка
   принимается в обеих записях — спековой `workspace.willRenameFiles` и
   pyright'овской `workspace.fileOperations.willRename`; хаб обязан заявить
   `workspace.willRenameFiles` в `HUB_CAPABILITIES`, иначе basedpyright правки
   импортов не шлёт; в отличие от wsym здесь сливаются ответы ВСЕХ поддерживающих
   серверов) -> cb через queued-мост `_RenameSignals` + токен-карту
   `_pending_renames` (чистится при смене корня и в `teardown_views`) -> слитая
   правка через `doc_ctrl.on_workspace_edit_requested({'origin': 'rename', ...})`
   -> повторная проверка `new.exists()` (имя могли занять, пока ждали ответ) ->
   `old.rename(new)` -> `hub.did_rename_files(...)` fire-and-forget (серверам
   обновить индекс) -> переоткрытие вкладки + `tree_view.reveal_path`; сбой LSP
   НИКОГДА не блокирует дисковое переименование. Create/delete LSP-нотификаций
   не шлют (v1).
   Сессия проекта (этап 9.6A): чистый `project_control/project_session.py` —
   формат `<root>/.pyqtp/session.json` = `{"docs": [...абс. пути в порядке
   открытия], "active": путь|null}`; `parse_session` недоедливый (битый
   JSON/мусор -> пустая сессия), `write_session` атомарный (`tempfile.mkstemp`
   + `Path.replace`), пустые вкладки -> `clear_session` убирает файл и пустой
   `.pyqtp/`; `filter_session` — «только внутри корня» (перенос папки).
   Учёт активной вкладки в `DocumentManager` — гибрид: KDD python-привязки
   2.x НЕ эмитят `isCurrentTabChanged` при смене вкладки/`setAsCurrentTab` и
   не экспортируют `isCurrentTab()`, а `view.hasFocus()`/
   `QApplication.focusWidget()` в offscreen врут; поэтому per-view сигнал
   `ensure_visible_requested` пишет `_last_activated_key`, а на момент
   снапшота (`session_snapshot`) он перекрывается подъёмом по родителям от
   `self._main_wnd.focusWidget()` — НЕ `QApplication.focusWidget()`: Qt
   хранит фокус per-top-level, `setFocus`/`focusWidget` работают и на
   неактивном/скрытом окне (на этом держатся offscreen-тесты без show()).
   Закрытие активной вкладки активирует соседку по порядку открытия —
   позицию снимать ДО удаления из `_opened_docs` (после — off-by-one).
   `close_project()` = сохранить сессию -> снять корень -> закрыть вкладки
   под корнем (`documents_under`+`close_document`, грязные остаются открыты
   с предупреждением); `open_project()` при смене корня сначала закрывает
   старый проект; restore — в `setup_views()` (старт с персистнутым в
   QSettings корнем) и в `_on_root_for_scan()` (рантайм; двойного restore
   нет — setup_views сигнал не эмитит); `on_shutdown` тоже сохраняет. API:
   `DocumentManager.open_documents/active_document_get/session_snapshot/
   document_view`, `ProjectControl.session_snapshot` + console
   `project_session_show`. В GUI-тестах сессии фейк `FakeDocView` обязан
   полностью соответствовать протоколу `Document` (в т.ч. конкретный `save()`
   — Protocol структурный, дефолт не наследуется) и держать фокусируемого
   ребёнка (`focus_child`, StrongFocus) для проверки фокус-обхода; тип
   сужается хелпером `fake_view()` (`document_view()` возвращает Optional).
   Per-project раскладка дока отложена (глобальная уже в
   `MainWindowKDD/dockState`).
   Настройки проекта (этап 9.6B1): чистый `project_control/project_settings.py`
   — оверлей `{секция: {ключ: значение}}` в `<root>/.pyqtp/settings.json`
   (секции `appearance`/`editor`/`lsp`, ключи `theme_name`/`EditorOptions`-ключи/
   `registry_json`); `parse_overlay` недоедливый (не-словари и пустые секции
   вычищаются, неизвестные секции сохраняются при правках), запись атомарная,
   пустой оверлей снимает файл и пустой `.pyqtp/` (не трогая `session.json`),
   `overlay_set(None)` снимает ключ. Проектное перекрытие СТАРШЕ настроек
   приложения, но правки приложения его НЕ «пробивают», а `settings_write`
   НЕ «утаскивает» проектные значения в QSettings (общее правило двухслойности):
   тема — `ThemeMainWindow.theme_app_name` + хуки `handle_theme_switch`
   (уголковый комбик: при активном проектном перекрытии пишет в слой проекта —
   `IdeMainWindow` переопределяет; core-версия = `theme_set_app`) и
   `theme_on_app_changed` (IDE пропускает apply, пока тему держит проект;
   `theme_settings_write` пишет `theme_app_name`); редактор —
   `DocumentManager.editor_ux_options()` = effective (QSettings +
   `merge_editor_ux_options` секции `editor`, битые значения молча игнорируются
   через `coerce_editor_ux_value`) vs `editor_ux_options_app()`, широкое
   переприменение — `broadcast_effective_editor_ux_options()`; LSP-реестр —
   `LspHub._registry_app_json/_registry_project_json` + `_effective_registry`
   (проект > приложение > дефолт, битый слой раскрывается в нижележащий),
   `registry_app_json()` для страницы, `registry_apply_json/reset` трогают
   только слой приложения, `registry_set_project_overlay(text|None)` — от
   ProjectControl. Страницы настроек в области «Приложение» грузят СЛОЙ
   ПРИЛОЖЕНИЯ (`theme_app_name`/`editor_ux_options_app`/`registry_app_json`).
   Двухслойность видна и в GUI (9.6B2): реестр областей
   `SettingsControl.scope_register(key, label, is_available)` +
   `scope_options()`/`scopes_refresh()` — `'app'` («Приложение») регистрирует
   ядро в `__init__`, `'project'` («Проект») — `ProjectControl.setup`
   (доступность = корень открыт, пересборка селектора на
   `project_root_changed`); фасады `settings_scope_register/
   settings_scopes_refresh` на BaseMainWindow. У `SettingsPage` —
   `scope_applicable` (окно показывает строку «Область» только для таких
   страниц) и `scope`/`scope_set()` (константы `SCOPE_APP` в
   `settings_page.py`, `SCOPE_PROJECT` в `project_control.py`); страница
   сама смотрит на `self.scope` в `load/apply/reset_defaults`. В
   `SettingsWindow` строка «Область: [QComboBox]» над стеком
   (`_build_scope_row`), `_sync_scope_row` при выборе страницы и
   `scopes_refresh()` (публичный хук) при смене доступности; если текущая
   область страницы пропала (проект закрыли при открытом окне) — откат на
   `'app'` + `page.load()`; программная пересборка комбика под флагом
   `_scope_syncing` не грузит страницу. Scope-aware страницы на своих
   местах (тот же key/order): «Редактор»/«LSP» в проектной области грузят
   ЭФФЕКТИВные значения, apply пишет слой проекта (редактор — все 5 ключей
   одним пакетом через новый `ProjectControl.project_settings_update(
   section, values)` — валидация ВСЕХ ключей до записи, одна запись файла,
   один emit), reset снимает секцию и сам зовёт `load()`; core-страницу
   «Внешний вид» в IDE заменил `AppearanceScopePage`
   (`main_wnd/appearance_scope_page.py`, та же key `appearance`/order 0,
   регистрация через `settings_page_unregister('appearance')` +
   `settings_page_register` в `IdeMainWindow.init_controls`): reset в
   проектной области снимает оверлей темы, а не пишет «Светлая». Запись строгая: `ProjectControl.project_settings_set/reset`
   валидируют секцию/ключ/значение (`ValueError`/`TypeError`; `font_size`-не
   строка -> TypeError, вне диапазона -> ValueError), затем файл -> состояние
   -> `_apply_project_layers()` (тема override-или-откат -> broadcast опций ->
   LSP overlay) -> emit `project_settings_changed`; применение при старте — в
   `setup_views()` ДО restore сессии, в рантайме — в `open_project()` до эмита
   корня, снятие — в `close_project()` после сохранения сессии (файл на диске
   при закрытии не трогается). Console-API `project_settings_show/set/reset`.
   В offscreen-тестах редактор фейкается атрибутом `edit_widget` вьюхи
   (`_apply_editor_ux_options` берёт его через getattr) с рекордерами
   `options_calls`/`theme_calls`; у PySide6 6.6-стабов `findChild` НЕ generic —
   тип сужать `isinstance`, а не `is not None` (в core-тестах это не видно —
   там `wnd` без аннотации -> Any).
   Закладки проекта (этап 9.7): чистый `controls/bookmark_control/bookmarks.py`
   — формат `<root>/.pyqtp/bookmarks.json` = `{"bookmarks":[{"path","line"}]}`,
   толерантный разбор, канонический порядок (path,line)+дедуп на записи,
   атомарная запись, пустой набор снимает файл и пустой `.pyqtp/` (не трогая
   `session.json`/`settings.json`); `filter_bookmarks` срезает записи
   пропа́вших файлов (при открытии проекта срезка сразу уходит обратно в
   файл), `toggle_bookmark`/`bookmarks_for`/`next_bookmark_line` (цикл).
   Единый владелец состояния — `BookmarkControl` (`controls/bookmark_control/
   bookmark_control.py`, регистрируется в `IdeMainWindow.init_controls` после
   `ProjectControl`, `bookmark_ctrl`): файл пишется сразу при каждом
   изменении (`toggle_at`/`remove_at`/`clear_project_bookmarks`; отказы — нет
   проекта / вне корня / файла нет), закладки живут вместе с проектом
   (`project_root_changed` -> `_on_root`; закрытие проекта файл на диске НЕ
   трогает); меню «Закладки» (PriorityMenu priority=-7) — toggle Ctrl+Alt+L /
   next Ctrl+Alt+K / prev Ctrl+Alt+J (связки VS Code); каретка для
   toggle/next/prev берётся из python-зеркала `edit_widget.current_cursor`
   (это property у `qtmonaco.Monaco`), переход — `set_cursor(...,
   move_to_position='center')`; console-API `bookmarks_show`; логгер
   `bcad.book` (группа «Проект»). Панель `BookmarksView`
   (`views/bookmarks_view.py`, `widget_area='right'`) пассивная: только
   `set_bookmarks(root, items)`, намерения наружу (`navigate_requested` в
   форме payload `open_definition_uri`, `remove_requested`,
   `clear_all_requested`; Delete по child-элементам, заголовки файлов —
   нет). Раздача строк редакторам: `_wire_doc_view` на `document_opened`
   пушит строки своего файла и один раз вешает `edit_widget.
   bookmark_toggle_requested -> toggle_at` (флаг `_pyqtp_bookmark_wired` на
   объекте вьюхи, как `_pyqtp_close_filter` у доков; `MonacoDocumentView` не
   трогали — и бы двойное переключение); любое изменение набора —
   `_apply_new_bookmarks` -> панель + broadcast ВСЕМ открытым документам
   проекта (дешево и всегда согласно). Порядок при смене корня (принят и
   закреплен тестами): сессия (9.6) поднимает вкладки раньше — первой им
   приходит пустая раздача, финальные строки доносит `_on_root` broadcast.
   JS-сторона (vendored-patch `main.ts`+`style.css`): свой `bookmarkCollection`
   (отдельно от `decorationsCollection` из `highlight_lines`), глиф
   `.pyqtp-bookmark-glyph` + подсветка строки, bridge-случай `set_bookmarks`
   (raw list, буферизуется коннектором), переприменение в `setActiveEditor` и
   во всех трёх ветвях `set_text`, отсечка строк за концом текста; клик по
   дорожке (`GUTTER_LINE_DECORATIONS`) -> `_toggle_bookmark_request {line}`
   -> Python `QtMonacoEditorWidget.bookmark_toggle_requested` (отсев bool /
   line<1); плюс `_current_cursor`-эхо на каждом `onDidChangeCursorPosition`
   (апстрим обновлял зеркало только echo-`set_cursor` — реальная каретка была
   не видна). В offscreen-тестах фейк-редактор обязан давать
   `bookmark_toggle_requested` (Signal(int)), `set_bookmarks`-рекордер,
   `set_cursor`-рекордер и атрибут `current_cursor` (dict).
   Панель «Проблемы» (этап 10.1): агрегация — на python-стороне хаба, НЕ
   чтением модели маркеров Monaco из JS (хаб видит весь `publishDiagnostics`;
   без вендорного TS-патча; проблемы живут и для закрытых документов). Чистый
   `controls/problems_control/problems.py` — frozen `Problem` (Monaco 1-based:
   LSP +1, `lsp_header` в живом IDE не вызывается), толерантный
   `diagnostics_from_publish` (нет `severity` = Ошибка, `code` int -> str,
   не-файловые uri -> `('', [])`), подписи серьёзности. Стор `LspHub`:
   `dict[(uri, server_name)] -> tuple[Problem, ...]` под `_diag_lock` +
   `add/remove_diagnostics_observer` + `diagnostics_snapshot()` (слитые по uri
   списки); сервер пишет через `DiagHooks(publish,purge)` из своего loop-потока
   (пустой список снимает пару, смерть процесса — весь вклад сервера, `shutdown()`
   — весь стор с оповещением); `owner` проставляется в сторе через
   `dataclasses.replace`. `ProblemsControl` (после `bookmark_ctrl`) —
   queued-мост `_DiagSignals` в GUI + обновление на `project_root_changed`;
   вьюха `views/problems_view.py` (`widget_area='bottom'`): двойной клик ->
   payload `{'uri','range':{'startLineNumber','startColumn'}}` ->
   `open_definition_uri`; console-API `problems_show`; экшены/F8/статус-бар —
   10.2. В live-тестах stора — детерминированный фейковый LSP-сервер
   (`python <скрипт>` в реестре) + ws-клиент `websockets.sync.client.connect
   (f'ws://{hub.ws_hosts_for(uri)[N]}')` (хост от хаба БЕЗ схемы `ws://`);
   GUI-тесты панели переиспользуют фейк-документы `test_bookmarks` импортом
   (тесты одного каталога — соседство в sys.path pytest).

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
  capabilities (no providers registered on the JS side). Stage 9.4 add-on:
  `workspace_symbols_async(project_root, query, cb, timeout)` is the GUI-thread
  entry (coroutine goes to its own loop; `_wsym_gen` generation gate suppresses
  cb of stale requests — their futures still resolve). Servers are picked
  without an open editor via `LspRegistry.sample_file_for(root, language)`
  (sorted os.walk for a sample file of each registry language, same prune as the
  tree/quick open) → `servers_for_uri` → first server declaring
  `workspaceSymbolProvider` answers; processes reuse the `(command, root_uri)`
  pool, `_wsym_samples` cache is cleared by `registry_apply_json`/`registry_reset`.
  Concurrency + cold start: `_ServerInstance._ready` (`asyncio.Event`, set in
  `start()` finally) — concurrent `_ensure_instance` calls await the handshake
  instead of spawning a second process on the same key; basedpyright/clangd
  answer cold (`[]`, not an error) while building their initial index after the
  handshake (~2 s on a small project) — hence `_ServerInstance.request_local_warm(
  method, params, reply_timeout, is_empty)`: one cold-retry per (process, method)
  (`cold_checked` budget + `_COLD_RETRY_DELAY`, shared by `workspace/symbol` and
  `workspace/willRenameFiles`), and concurrent same-method empty answers await
  each other's warm and re-ask instead of burning the budget (race found by
  `test_live_workspace_symbols_cb_and_stale` — a about-to-be-stale request must
  not leave the current one with an empty answer);
  `HUB_CAPABILITIES['workspace']['workspaceSymbol']` must be advertised or the
  server never enables the feature.
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
  stage 9.7 bookmarks (`main.ts` + `style.css`) — separate `bookmarkCollection`
  (upstream `decorationsCollection` stays owned by `highlight_lines`),
  `applyBookmarks()`/`setBookmarks()` + bridge case `set_bookmarks` (raw list
  payload, buffered by the Connector) and `window.qtmonaco.bookmarkLines`;
  glyphs re-applied in `setActiveEditor` and all three `set_text` branches,
  lines past the model end are dropped; gutter clicks via
  `GUTTER_LINE_DECORATIONS` (`onLineDecorationClicked`) send
  `_toggle_bookmark_request {line}` to Python; cursor echo — `_current_cursor`
  is sent on EVERY `onDidChangeCursorPosition` (upstream mirrored it only on
  the `set_cursor` echo, so the real caret was invisible to Python); CSS
  classes `.pyqtp-bookmark-glyph` (#e5a64b) + `.pyqtp-bookmark-line`.
  python patch in `monaco.py` —
  `Monaco.__init__` takes an `lsp_host` param and no longer eagerly spawns
  `get_pylsp_host()` (lazy `_resolve_lsp_host()` fallback keeps standalone
  behavior; stage 2). Rebuild `js_build` after any TS change. Keep it
  out of ruff scope (its files are the 3 baseline errors); pyright excludes it via
  `packages/pyrightconfig.json` (imported types still resolve).

## Conventions

- **ЖЁСТКИЙ ЗАПРЕТ НА VISION (обработку изображений)** — сессии в этом репозитории работают без
  зрения: не читать и не анализировать изображения модели (PNG/JPEG/WebP/скриншоты, «визуальная»
  проверка GUI через Read-картинку, сравнение скриншотов, генерация изображений — запрещены).
  Любую проверку GUI/редактора делать программными пробами: `runJavaScript` со строковыми
  маркерами, DOM-классы (`.pyqtp-bookmark-glyph`, `.monaco-editor-background`), computed-стили/цвета,
  сигналы и состояния Qt, файлы на диске. Чтение текстовых файлов (в т.ч. `.svg` как XML,
  стили, QSS) — НЕ vision, разрешено.
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
