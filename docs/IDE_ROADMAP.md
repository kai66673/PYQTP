# PYQTP IDE — дорожная карта развития (после исследования стека)

Документ фиксирует **итог исследования применимости стека** и план дальнейшего
развития PYQTP до полноценной IDE. Эталон сравнения — референсное приложение
`iide` (/home/kai/kaigit/iide, Vala/GTK4/libpanel/GtkSourceView; проведён
полный аудит исходников: ~100 файлов Vala).

Предшествующие этапы (1–6.2) задокументированы в `docs/LSP_PLAN.md`.

---

## 1. Вердикт исследования

**Стек PySide6 + Monaco(QWebEngine) + собственный LSP-хаб + KDDockWidgets
применим для полноценной IDE.** Подтверждено живыми тестами и ручной
проверкой (этапы 1–6.2):

| Проверяемая способность | Итог |
|---|---|
| Редактор Monaco в QWebEngineView, мост Python↔JS (QWebChannel) | работает; редактор поднимается за ~2–3 с (тёплый кэш) |
| LSP-хаб: ws-сервер, пул процессов, мультиплексирование id, replay initialize | работает; реальные basedpyright+ruff и clangd |
| Диагностика (publishDiagnostics → Monaco markers) | работает, live-тесты |
| completion / hover / signatureHelp (стеки серверов, claimProvider) | работает, live-тесты |
| completion по `.` поверх дебаунса didChange (этап 6.2) | исправлено (`flushDocumentChanges`), live-тест |
| semanticTokens (полный ответ → Monaco) | работает, live-тест |
| Междокументные переходы (definition в другой файл → вкладка+курсор+фокус) | работает, live-тест |
| Док-система KDD: вкладки, закрытие, Qt-фокус | работает |
| Встроенный UX Monaco: поиск/замена, складки, сварт-селект, зум шрифта, история jump (Alt+←/→) | работает, webengine-тесты + страница настроек «Редактор» |
| Регрессия: 34 pytest-теста (17 live/webengine), ruff/pyright базы не меняются | работает |

Главный **осознанный trade-off** — редакторы на QWebEngine тяжелее нативных
(GtkSourceView). Цифры — в разделе «Метрики» ниже.

## 2. Gap-карта: PYQTP ↔ iide

Что есть в iide и чего пока нет в PYQTP (по итогам аудита):

| Подсистема | iide | PYQTP | План |
|---|---|---|---|
| LSP-клиент: hover/completion/definition/formatting/rename/willRename+didRename/workspace+documentSymbol/codeActions/pull-диагностики/applyEdit | реализовано в `LspClient.vala` (~1900 строк ручного JSON-RPC) — весь основной набор | completion/hover/signatureHelp/definition/semanticTokens + publishDiagnostics | **этап 8** |
| references / signatureHelp в iide | declared в клиенте и capabilities; проводки в UI по коду не видно (вероятно, очередь до UI не дошла) | signatureHelp есть; references — этап 8 | — |
| inlayHint / semanticTokens в iide | нет (inlayHint сложно в GtkSourceView; semanticTokens компенсируются tree-sitter) | semanticTokens **есть** (этап 5); inlayHint — опция этапа 8+ | — |
| Find/replace, folding, minimap, multi-cursor, jump history | частично своё, на GtkSourceView | **бесплатно встроено в Monaco** — только команды/шорткаты/опции UI | **этап 7** |
| Дерево проекта, сессии, quick open, поиск по проекту | есть (файловая панель; fuzzy-поиск свой; сессии в GSettings) | нет | **этап 9** |
| Problems-панель, статус, мониторинг LSP | есть (`LspMonitorPanel` — сильная фича) | нет | **этап 10** |
| Отладчик DAP | есть (debugpy/lldb; **без F5/F10/F11**) | D1 **есть** (debugpy stdio, F5/F9/F10/F11, консоль) — панели D2/D3 нет | **этап 11 (D1 готово)** |
| Tree-sitter (15 грамматик, 5 рабочих) | есть | не нужно — роль закрывают Monaco + semanticTokens | не переносим |
| Закладки/брейкпоинты в JSON проекта | есть | закладки **есть** (9.7), брейкпоинты **есть** (11.1) | закрыто |
| Плагины/расширяемость | нет | **есть в архитектуре** (plugins, Python-консоль) | **этап 12** — наше преимущество |
| CLI, welcome-экран, недавние проекты | нет | нет | этап 12 |
| Git | нет у обоих | — | этап 13 (отложен) |
| Тесты | нет | **есть** (pytest + live/webengine-маркеры) | держим планку |

## 3. Этапы 7–13

### Этап 7 — «бесплатный» Monaco: встроенные фичи в UI ✅ (готово)
Всё уже есть в Monaco; нужно лишь включить/назначить:
find/replace (regex/case/word), folding, minimap, bracket pairs + автопары,
multi-cursor, expand selection, zoom, jump history Alt+←/→.
Правки: `packages/qtmonaco/backend/src/main.ts` (патч вендора: `multiCursorModifier:
"alt"`, `scrollBeyondLastLine: false` в дефолты; правила `addKeybindingRules` —
зум шрифта `Ctrl+=`/`Ctrl+-`/`Ctrl+0`, smart select `Ctrl+Shift+.`/`,`, история
переходов `Alt+←/→`; свои команды `qtmonaco.editor.navigation.goBack/goForward`
+ собственный стек позиций (в standalone-бандле workbench navigation history
нет); слушатель каретки привязан к редактору через `attachEditorListeners`,
отправная точка истории (`navLastPos`) засеивается текущей позицией после каждой
смены модели/редактора и подмены текста (`seedNavHistory`) — иначе первый крупный
переход после открытия файла в историю не попадал) →
пересборка `js_build`. Опции (minimap/folding/зум колесом/размер шрифта) —
страница настроек «Редактор» (`EditorSettingsPage`, key `editor`, order 10),
хранение в QSettings-группе `EditorOptions` (`pyqtp/web/editor_options.py`),
рассылка в открытые редакторы через `DocumentManager.apply_editor_ux_options`
(`update_editor_options`, буферизуется коннектором до инициализации моста).
Связки Ctrl+F/H и Ctrl+K- chords folding уже были встроены в Monaco.
Дополнение — **тема редактора**: ключ `theme` (auto/light/dark) в том же словаре
`EditorOptions`; `auto` (по умолчанию) следует теме приложения (`Theme.is_dark`,
как с цветами IPython-консоли), light/dark — фиксированные встроенные темы Monaco
`vs`/`vs-dark` (своя тема из палитры приложения через `defineTheme` сознательно
отложена). Тема НЕ входит в payload `update_editor_options` — разрешается чистой
функцией `resolve_monaco_theme(options, app_is_dark=)` и уходит отдельным
сообщением `theme` (`Monaco.set_theme`); `DocumentManager` применяет её при
открытии документа и широковещании, а также переприменяет открытым документам по
сигналу `theme_manager.theme_changed`. В `main.ts` — патч `appliedTheme`
(запоминание темы, чтобы пересоздание standalone-редактора после diff-режима и
сам diff-режим не откатывали тему к `vs-dark`) и `autoDetectHighContrast: false`
(иначе ОС с high-contrast молча подменяет выбранную тему на `hc-*`).
Комбик «Тема редактора» — на странице «Редактор».
**Приёмка:** webengine-тесты `tests/test_editor_ux_stage7.py` (опции из
Python, find/replace, folding, smart select, история переходов, зум,
регистрация связок; тема: переключение из Python и сохранение темы после
возврата из diff-режима), безмаркерные `tests/test_editor_options_theme.py`
(`resolve_monaco_theme`, валидация, roundtrip QSettings); живая проверка
смены темы приложения.

### Этап 8 — завершение LSP-клиента
По образцу существующих фабрик провайдеров (`claimProvider`,
`flushDocumentChanges`):
- `textDocument/documentSymbol` → breadcrumbs/outline;
- `workspace/symbol` → quick open символов (связка с этапом 9);
- `textDocument/references` → провайдер + список/peek (переиспользует
  инфраструктуру переходов этапа 6: `_open_uri_request`);
- `textDocument/rename` (+`prepareRename`) → диалог с предпросмотром
  WorkspaceEdit, применение через мост; `workspace/applyEdit` на Python-стороне;
- `textDocument/formatting` (Shift+Alt+F), `textDocument/codeAction` +
  quick fix (Alt+Enter) в контексте диагностики;
- опционально: `inlayHint`, `documentHighlight`, `codeLens`;
- динамическая регистрация capabilities — по факту (basedpyright/clangd
  работают статически).
**Приёмка:** live-тест на каждую фичу (паттерн `test_lsp_editor_stack`).

#### Этап 8.1 — references, documentSymbol, documentHighlight ✅ (готово)
- `textDocument/references` → `createReferencesProvider` (vendored-patch в
  `providers.ts`): встроенная команда `editor.action.goToReferences` (связка
  `Shift+F12` уже зарегистрирована самим contrib'ом — своих правил не добавляли);
  при >1 результате Monaco открывает peek `referenceSearch`, переходы из peek по
  чужим файлам идут через opener этапа 6 (`_open_uri_request` →
  `open_uri_requested`); разбор `Location[]`/`LocationLink[]` вынесен в общий
  `mapLspLocations` (его же использует definition-провайдер этапа 4);
- `textDocument/documentSymbol` → `createDocumentSymbolProvider`: хаб заявляет
  `hierarchicalDocumentSymbolSupport`, иерархия рекурсивно маппится в
  `monaco.languages.DocumentSymbol` (таблица числовых kind LSP отличается от
  Monaco-enum ровно сдвигом `-1`), плоский `SymbolInformation[]` тоже
  поддерживается; breadcrumb-виджета в standalone-бандле этого Monaco нет
  (в esm-дереве только `diffEditorBreadcrumbs`), поэтому навигация по символам
  документа отдана встроенному **sticky scroll** (`stickyScroll: {enabled: true}`,
  `defaultModel: outlineModel` строит модель из того же провайдера);
- `textDocument/documentHighlight` → `createDocumentHighlightProvider`:
  семантическая подсветка вхождений в текущем документе (LSP kind Text/Read/Write
  → Monaco 0/1/2: `wordHighlightText`/`wordHighlight`/`wordHighlightStrong`);
  при наличии провайдера wordHighlighter использует его, поэтому текстовые
  совпадения одноимённых локальных имён не подсвечиваются;
- регистрация в `lsp.ts` (`ensureProvidersRegistered`): гейт по InitializeResult
  сервера (`referencesProvider`/`documentSymbolProvider`/
  `documentHighlightProvider`) + `claimProvider` — ровно один провайдер на язык
  (Monaco не объединяет references/outline/highlight, в отличие от
  completion/hover); Python-хаб не менялся — его `HUB_CAPABILITIES` уже
  содержали нужные записи.
**Приёмка:** live-тесты `test_stage81_references_peek_and_cross_document`
(модель refs + peek-DOM + `open_uri_requested` из peek по чужому файлу),
`test_stage81_document_symbols_and_sticky_scroll` (outline-иерархия через
`_executeDocumentSymbolProvider` + DOM sticky scroll),
`test_stage81_document_highlight_semantic` (набор строк write/read модульной
переменной без текстовых ложных срабатываний). Остаток этапа 8
(formatting/rename+applyEdit/codeAction — 8.2) отдельным заходом;
`workspace/symbol` — за этой же чертой (связан с этапом 9).

#### Этап 8.2 — rename, formatting, codeAction + WorkspaceEdit через мост ✅ (готово)
- общий разбор WorkspaceEdit (`splitWorkspaceEdit`/`foreignWorkspaceEditPayload`
  в `providers.ts`): ответ сервера режется на **локальную** часть (текущая
  модель — формат Monaco `{edits:[{resource, textEdit:{range,text}}]}`,
  `versionId` намеренно опущен: сервер строил правки по своему снимку, проверка
  версии у bulkEditService отменила бы всю правку целиком) и **чужую**
  (закрытые/чужие документы и file-ops Create/Rename/Delete) — она уходит в
  Python сообщением `_apply_workspace_edit_request` `{origin, sourceUri,
  workspaceEdit:{documentChanges}}`; форма `changes` нормализуется в
  `documentChanges` на JS-стороне, порядок сохраняется (сервер мог создать
  файл и сразу его править);
  причина разделения именно такая: `StandaloneBulkEditService.apply` —
  all-or-nothing («bad edit - model not found» на отсутствующей модели,
  file-ops не умеет вовсе);
- `textDocument/rename` (+`prepareRename`) → `createRenameProvider`,
  single-owner (`claimProvider("rename")` — правка rename применяется одним
  batch'ем, второй сервер удвоил бы правки). Встроенный F2-виджет Monaco
  (`editor.action.rename`): `resolveRenameLocation` разбирает `PrepareRenameResult`
  (Range | {range,placeholder}), в этом билде Monaco передаёт `(model, position,
  token)` без options — аргументы разбираются хвостом. Своя часть применяется
  Monaco; чужая (переименование импорта в другом файле) шлётся в Python сразу
  после ответа сервера (F2 уже подтверждён пользователем), с последующей
  правкой модели по одной undo-точке;
- `textDocument/formatting` + `rangeFormatting` → `createFormattingProviders`
  (команды `editor.action.formatDocument`/`formatSelection` — штатные contrib
  Monaco, своих биндов не добавляли), single-owner под общим claim
  «formatting» (второй форматтер переписал бы результат первого), ответ —
  обычный `TextEdit[]` в текущую модель;
- `textDocument/codeAction` (quickfix/refactor/source) →
  `createCodeActionProvider`: регистрируется **каждым** сервером стека без
  claim (Monaco объединяет меню фиксов; у pyright/ruff наборы непересекаются).
  Контекст диагностики берётся из кэша **сырых** `publishDiagnostics` в
  `LspClient` (`publishedDiagnostics`), а не из маркеров Monaco: MarkerService
  вырезает неизвестные поля, а basedpyright требует roundtrip своего `data`.
  `codeAction/resolve` регистрируется всегда (сервер может отдать действие без
  `edit`). Отложенная чужая часть: токен в `pendingForeignEdits` (main.ts) +
  `action.command = qtmonaco.lsp.applyForeignEdit(token)` — Monaco в
  `applyCodeAction` исполняет `action.edit` (bulkEditService), **затем**
  command, порядок local→foreign гарантирован; действие только с чужими
  правками остаётся в меню без `edit`;
- применение чужой части на Python (`pyqtp/controls/doc_control/
  workspace_edit.py` — чистые хелперы + оркестрация в
  `DocumentManager.on_workspace_edit_requested`): открытый чужой документ
  получает сообщение `apply_lsp_text_edits` в свой редактор (проверка uri
  модели, `model.pushEditOperations` — одна undo-точка; обычный
  `model.applyEdits` не подошёл: он по умолчанию не undoable), ответ
  `_workspace_edit_applied`, dirty-трекинг работает через `_current_text`
  автоматом); закрытый — чтение диска + `apply_text_edits_to_text` (колонки
  LSP — единицы UTF-16, учтены суррогатные пары) + файловые операции с
  `overwrite`/`ignoreIfExists`/`recursive`;
- `workspace/applyEdit` в хабе — осознанная заглушка `applied:false`
  (`applyEdit: False` в `HUB_CAPABILITIES`): серверы (pyright/ruff/clangd)
  кладут правки в `edit` действий/ответов, server-initiated применение им не
  нужно; сервер с `workspace/executeCommand` вместо `edit` на этой версии не
  поддерживается (действие попадает в меню, но edit у него отсутствует).
**Ограничения v1 (приняты как данность):**
  чужой **открытый dirty**-документ может получить правки по устаревшему
  снимку сервера (debounce `didChange` 300 мс); `versionId` в локальных
  правках опущен (см. выше); ресурсные операции над открытыми документами
  выполняются на диске без переименования/закрытия вкладок (в журнал —
  warning).
**Приёмка:** юнит-тесты `tests/test_workspace_edit_apply.py` (16 — разбор
форм, UTF-16-колонки, файловые операции) + live-тесты
`test_stage82_rename_same_document` (F2 целиком в модели, виджет переживает
offscreen без Qt-фокуса — rename-прогоны намеренно без `ed.focus()`),
`test_stage82_rename_cross_document` (прайминг F12, локальная часть в буфере,
чужая — сигнал `workspace_edit_requested` + применение чистыми хелперами,
диск чужого файла не тронут), `test_stage82_formatting_document` (ruff
whole-document edit + одна undo-точка), `test_stage82_code_action_quickfix`
(список фиксов basedpyright через `apply:'never'`, применение первого,
уход диагностики), `test_stage82_apply_lsp_text_edits_direct` (мост
Python→JS: uri-фартук, одна undo-точка, warning `bcad.med` на ok:false).
Остаток этапа — `workspace/symbol` — закрыт этапом 9.4; `inlayHint`/
`codeLens`/`documentColor` сознательно не брали.

### Этап 9 — проект и сессии

#### 9.1 — «Открыть папку» + корень проекта + панель дерева — ГОТОВО
- `ProjectControl` (`pyqtp/controls/project_control/project_control.py`):
  корень проекта в QSettings (`ProjectControl/root_path`, пропавшая папка
  при старте молча пропускается), меню «Файл» — «Открыть папку...»
  (Ctrl+Shift+O) / «Закрыть папку», console-API `project_root/
  project_open/project_close`;
- панель `ProjectTreeView` (`pyqtp/views/project_tree_view.py`, слева):
  ленивый `QFileSystemModel` + proxy-фильтр (точечные записи, `__pycache__`,
  `node_modules`), двойной клик по файлу → `DocumentManager.open_document`,
  reveal активного документа по `document_opened` (асинхронная цепочка
  `fetchMore`-шагов; контекстного меню пока нет);
**Приёмка:** `tests/test_project_tree.py` (8 offscreen-GUI тестов без
LSP/WebEngine: заполнение панели/фильтр мусора, двойной клик, reveal
вложенного файла, персист корня между перезапусками, снятие при закрытии).

#### 9.2 — quick open (Ctrl+P) — ГОТОВО
- `quick_open.py` (`pyqtp/controls/project_control/`): DP fuzzy-скоринг
  (`fuzzy_score`: регистронезависимое подпоследовательностное вхождение,
  бонусы старт/граница/подряд; граница бонусится только первому символу
  query, иначе «p после точки» в `deep.py` обгонял бы цепочку `dp.py`;
  итог — только за полностью совпавшую query) + синхронный скан
  `iter_project_files` (тот же prune, что у дерева: точечные + `__pycache__`
  /`node_modules`);
- `ProjectControl`: фоновый скан (`threading.Thread` + queued-сигнал
  `_ScanSignals`) с защитой от запоздалых результатов поколением `_scan_gen`
  (инкремент при смене/закрытии проекта и teardown), экшен «Быстрый переход
  по файлам...» (Ctrl+P, «Файл» priority=7, активен только с проектом),
  console-API `quick_open_show`;
- `QuickOpenDialog`: немодальный, QLineEdit + QListWidget (до 100 строк,
  сортировка по скорингу, при равном — порядок обхода), Up/Down/Enter из
  поля ввода через `eventFilter`, выбор → `file_selected` (абсолютный путь)
  → тот же `open_document`, что у дерева.
**Приёмка:** `tests/test_quick_open.py` (13: 7 юнитов скоринга + 2 юнита
сканера без GUI + 4 offscreen-GUI — гейт экшена по проекту, скан→диалог→
Enter открывает документ, набор fuzzy-строк, обнуление кэша и сброс
диалога при закрытии проекта + отброс запоздалого скана по поколению).

#### 9.3 — поиск по проекту (Ctrl+Shift+F) — ГОТОВО
- `project_search.py` (`pyqtp/controls/project_control/`): сборка argv ripgrep
  (`build_rg_args`: `--json --line-number --no-messages`, `-i` по умолчанию
  снимается галкой «Регистр», `-w` — «Слово целиком», `--fixed-strings`
  снимается галкой «Regex»; поиск из корня с аргументом `.`) + инкрементный
  парсер `--json` (`split_complete_lines` — хвост незавершённой JSON-строки
  остаётся в буфере; `match_from_event` — пути rg со `./` → абсолютные,
  байтовые смещения UTF-8 → UTF-16-колонки Monaco как в этапе 8.2, см.
  `workspace_edit.py`);
- панель `ProjectSearchView` (`pyqtp/views/project_search_view.py`, снизу):
  `QProcess` с `rg` держит сама вьюха (паттерн 9.1 — источник в вьюхе),
  перезапуск/смена корня/teardown снимают процесс, чужие сигналы отбрасываются
  поколением `_search_gen`; дерево «файл → строки» (галки Регистр/Слово
  целиком/Regex, статус-лейбл); срезка на `MAX_MATCHES=2000` c kill процесса;
  exit≥2 — «Ошибка: <последняя строка stderr>» (битый regex), rg не найден —
  FailedToStart;
- `ProjectControl`: экшен «Поиск по проекту...» (Ctrl+Shift+F, «Файл»
  priority=9 — после добавления 9.4 «Закрыть папку» на 10, активен только с проектом),
  console-API `project_search_show`; клик по совпадению →
  `DocumentManager.open_definition_uri` (тот же путь, что переходы LSP этапа 6).
**Приёмка:** `tests/test_project_search.py` (15: 2 юнита argv + 5 юнитов
парсера без GUI и без бинаря rg (записанные JSON-ивенты; кириллица и эмодзи
UTF-16-колонки) + 7 offscreen-GUI с живым rg (гейт экшена, группировка и
статус, UTF-16-колонка в payload открытия, regex/слово-целиком, пустой
результат/сброс при закрытии проекта, битый regex → «Ошибка»; плюс
гейт `skipif` при отсутствии rg).

#### 9.4 — переход к символу проекта (Ctrl+T, workspace/symbol) — ГОТОВО
- `workspace_symbol.py` (`pyqtp/controls/lsp_control/`): чистый разбор
  ответа сервера — спека LSP допускает две формы элемента: `SymbolInformation`
  (`location={uri, range}` + `containerName`) и `WorkspaceSymbol` 3.17
  (`location={uri[, range]}` + устаревшие верхнеуровневые
  `range`/`selectionRange`); битые элементы молча пропускаются, fallback
  без валидного range — `(1, 1)` (переход в начало файла); dataclass
  `WorkspaceSymbol` — уже в Monaco-координатах (1-based, тот же формат,
  что у `open_definition_uri`); `SYMBOL_KIND_NAMES` — русские тексты
  kind'ов 1..26;
- `LspHub.workspace_symbols_async(project_root, query, cb, timeout)` —
  вход из GUI-потока, корутина уходит в event-loop потока хаба. Выбор
  сервера без открытого редактора: `LspRegistry.sample_file_for()` находит
  обходом корня «образцовый» файл каждого языка реестра (тот же prune,
  что у дерева/quick open) → `servers_for_uri` → процесс переиспользуется
  из общего пула `(command, root_uri)`; отвечает первый, объявивший
  `workspaceSymbolProvider`; кэш образцов `_wsym_samples` сбрасывается при
  применении/сбросе реестра;
- хаб обязан заявить клиентскую возможность `workspace.workspaceSymbol`
  (иначе сервер не включает фичу) — есть regression-guard тест;
- надёжность (у хаба теперь два источника запросов, не только редакторы):
  `_ServerInstance._ready` (`asyncio.Event`, ставится в `finally start()`) —
  параллельные `_ensure_instance` ждут чужой хендшейк, а не плодят второй
  процесс на тот же ключ; холодный старт: pyright/clangd во время первичной
  индексации после хендшейка отвечают пустым списком (замер на basedpyright
  — индекс готов ~через 2 с), поэтому первый пустой ответ повторяется один
  раз на процесс+метод (механизм `request_local_warm` + `_COLD_RETRY_DELAY`,
  обобщён этапом 9.5); поколение
  `_wsym_gen` — запоздалые ответы устаревших запросов не доходят до cb;
- `WorkspaceSymbolDialog` (`pyqtp/controls/project_control/`): немодальный
  в стиле quick open (QLineEdit + QListWidget + статус), дебаунс 400 мс,
  cb из потока хаба — queued-сигналом `_SymbolSignals`, гейт поколения
  `_req_gen` на стороне диалога (`drop_pending()` при смене/закрытии
  проекта и teardown); выбор → `symbol_selected` (payload для
  `DocumentManager.open_definition_uri`);
- `ProjectControl`: экшен «Переход к символу проекта...» (Ctrl+T, «Файл»
  priority=8 — поиск переехал на 9, «Закрыть папку» на 10, активен только
  с проектом), console-API `workspace_symbols_show`.
**Приёмка:** `tests/test_workspace_symbol.py` (15: 10 юнитов — три формы
ответа + мусор/None, гейт `workspaceSymbolProvider: boolean|options`,
форма payload, замороженность dataclass, regression-guard возможности
хаба, 3 юнита `sample_file_for` с prune/точечными + 1 не-live GUI
(гейт Ctrl+T по проекту) + 4 live с реальным basedpyright — запрос через
хаб (MyClass, строка 4, колонка 7), доставка cb и устаревание по
поколению, пустой проект (понятная ошибка без зависания), offscreen-
диалог: запрос → дебаунс → список → payload открытия).

#### 9.5 — контекстное меню дерева: создать/переименовать/удалить в корзину — ГОТОВО
- контекстное меню — у `ProjectTreeView`: она только строит QMenu и сопоставляет
  позицию с путём (`indexAt`) и эмитит сигналы намерений
  `create_file_requested/create_folder_requested/rename_requested/delete_requested`;
  пустая область — только создание в корень проекта; диалогов и дисковой
  логики во вьюхе нет (всё в `ProjectControl`);
- `project_fileops.py` (`pyqtp/controls/project_control/`): чистые хелперы —
  валидация имени (без `/`, `\`, `.`, `..`), `create_entry` (файл/папка, без
  перезаписи), `renamed_path` (отказ при том же имени/занятом), `move_to_trash`
  через `QFile.moveToTrash()` — при недоступной корзине отказ с предупреждением,
  без незаметного безвозвратного удаления (квирка: рантайм PySide6 6.6
  возвращает `bool`, а стабы объявляют пару `(bool, путь_в_корзине)` —
  нормализованы обе формы);
- flow переименования (`ProjectControl._on_rename_requested`): диалог ->
  валидация -> при открытом документе с правками отказ (пусть сохранит),
  чистая вкладка закрывается молча заранее -> `LspHub.will_rename_files_async
  (root, old_uri, new_uri, cb)` -> cb через queued-мост `_RenameSignals` и
  токен-карту `_pending_renames` (чистится при смене корня/teardown) ->
  слитая правка через ту же точку входа этапа 8.2
  (`DocumentManager.on_workspace_edit_requested`) -> повторная проверка цели
  (пока ждали ответ сервера, имя мог занять кто-то другой) -> `Path.rename` ->
  `did_rename_files` (fire-and-forget — серверам надо обновить индекс) ->
  переоткрытие вкладки + `reveal_path`; **сбой LSP никогда не блокирует дисковое
  переименование** — только предупреждение;
- хаб: `will_rename_files_async`/`did_rename_files` (секция «Файловые
  операции»); серверы — стек языка переименовываемого файла
  (`servers_for_uri(old_uri)`), процессы — общий пул `(command, root_uri)`;
  в отличие от `workspace/symbol` здесь сливаются ответы ВСЕХ объявивших
  поддержку серверов (каждый правит «своё»: pyright — импорты, clangd —
  inclusion'ы); чистый разбор/мердж — `file_rename.py`
  (`pyqtp/controls/lsp_control/`); поддержку принимаем в обеих записи —
  спеки `workspace.willRenameFiles` и расширенной pyright'овской
  `workspace.fileOperations.willRename`; хаб обязан заявить
  `workspace.willRenameFiles` у себя (иначе basedpyright правки импортов не
  шлёт — regression-guard тест);
- холодный прогрев обобщён: `_ServerInstance.request_local_warm(method,
  params, reply_timeout, is_empty)` вместо wsym-специфичного `wsym_answered`;
  прогрев один на пару (процесс, метод) — `cold_checked`, а параллельные
  пустые ответы того же метода ждут чужой прогрев и переспрашивают
  (`cold_inflight`): иначе вот-вот устаревший запрос «сжёг» бы бюджет, а
  актуальный остался бы с пустым ответом (regression на
  `test_live_workspace_symbols_cb_and_stale`);
- `DocumentManager`: программные операции с документами — `has_document`,
  `document_is_modified`, `documents_under` (под удаляемым каталогом) и
  `close_document` (без диалогов, с отказом при правках); хвост закрытия
  central-дока вынесен в общий `_force_close_doc_dock`.

**Приёмка:** `tests/test_project_fileops.py` (20: 6 юнитов fileops — имя/
создание/переименование/корзина, включая реальный `move_to_trash`, + 6 юнитов
file_rename — обе записи возможности, форма params, пустой ответ, мердж,
regression хаба + 5 offscreen — набор меню (файл/каталог/пустая область/без
проекта) и пути сигналов, создание файла/папки + коллизия + отмена, rename
без LSP: дисковая правка импорта + переезд файла + didRenameFiles + отброс
устаревшего ответа, guard'ы rename: отказ при правках / закрыть-переоткрыть
/ сбой хаба не блокирует / занятое имя, delete: отказ No / отказ при
правках под путём / закрытие чистых документов перед корзиной + 3 live с
реальным basedpyright — willRenameFiles приносит правку импорта, cb всегда
приходит при пустом стеке серверов, сквозная приёмка переименования).

#### 9.6 (часть A) — сессия проекта: вкладки + активный документ — ГОТОВО
- хранение — файл `<root>/.pyqtp/session.json` (НЕ QSettings — сессия живёт
  вместе с проектом, переезжает вместе с папкой): формат
  `{"docs": [...абсолютные пути в порядке открытия], "active": путь|null}`;
- `project_session.py` (`pyqtp/controls/project_control/`) — чистый модуль:
  `SessionData` + `serialize_session/parse_session/deserialize_session`
  (разбор недоедливый: мусор/битый JSON -> пустая сессия), `read_session`,
  `write_session` (атомарно: `tempfile.mkstemp` + `Path.replace`),
  `clear_session` (пустые вкладки -> файл и пустой каталог `.pyqtp/` удаляются),
  `filter_session` (фильтр «только внутри корня» — на случай переноса папки);
- учёт активной вкладки — гибрид (прободы KDD 2.x: python-привязки НЕ эмитят
  `isCurrentTabChanged` при смене вкладки и не экспортируют `isCurrentTab()`):
  per-view `ensure_visible_requested` пишет `_last_activated_key`, а на момент
  снапшота он перекрывается Qt-фокусом — подъём от `self._main_wnd.focusWidget()`
  (НЕ `QApplication.focusWidget()`: Qt хранит фокус per-top-level, он работает и
  на неактивном/скрытом окне — на этом держатся offscreen-тесты; `hasFocus()`
  в offscreen тоже врёт);
- закрытие активной вкладки активирует соседку по порядку открытия (позицию
  снимаем ДО удаления из реестра — после удаления off-by-one);
- `close_project()`: сначала `_save_project_session()`, затем снятие корня и
  закрытие вкладок под корнем (`documents_under` + `close_document`; документы
  с правками остаються открыты с предупреждением — ничего не выбрасываем);
  `open_project()` при смене корня сначала проводит старый проект;
- восстановление — в `setup_views()` (старт с сохранённым в QSettings корнем)
  и в `_on_root_for_scan()` (открытие в рантайме; двойного восстановления нет —
  при старте сигнал не эмитится); отсутствующие файлы пропускаются с логом,
  активный опционально;
- `DocumentManager`: `open_documents()`, `active_document_get()`,
  `session_snapshot()`, `document_view()`; `ProjectControl.session_snapshot()`
  + console-API `project_session_show`; раскладку дока глобально уже держит
  `MainWindowKDD/dockState` (per-project раскладка отложена).

**Приёмка:** `tests/test_project_session.py` (16: 12 юнитов project_session —
формат/недоедливый разбор/атомарность/`clear_session`/`filter_session` +
4 offscreen — гибрид учёта вкладки (ensure_visible + Qt-фокус + сосед при
закрытии), цикл закрыть-переоткрыть проекта, пропуск удалённых файлов,
пустые вкладки очищают файл).

#### 9.6 (часть B1) — слой настроек проекта поверх приложения — ГОТОВО
- файл `<root>/.pyqtp/settings.json` содержит ТОЛЬКО перекрываемые проектом
  значения `{секция: {ключ: значение}}` — настройки приложения остаются
  дефолтом и для режима «без проекта», и для впервые открытой папки;
  перекрытие поключевое, известные секции: `appearance` (`theme_name`),
  `editor` (ключи `editor_options`), `lsp` (`registry_json`);
- `project_settings.py` (`pyqtp/controls/project_control/`) — чистый модуль:
  `parse_overlay` (недоедливый: не-словари/битый JSON -> пустой слой, пустые
  секции вычищаются, неизвестные секции сохраняются при правках),
  `read/write_project_settings` (запись атомарная; пустой оверлей removes
  файл и пустой `.pyqtp/`, чужой `session.json` не трогается), `overlay_set`
  (`None` снимает ключ);
- чтение недоедливое, запись СТРОГАЯ: `ProjectControl.project_settings_set/
  reset` валидируют секцию/ключ/значение (`ValueError`/`TypeError` с текстом)
  и после успеха перечитывают состояние, переприменяют слои и эмитят
  `project_settings_changed`; console-API `project_settings_show/set/reset`;
- применение — `_apply_project_layers()` при открытии/закрытии проекта и
  runtime-правках: тема (оверлей, иначе откат к `theme_app_name`) ->
  широковещательное переприменение ЭФФЕКТИВных UX-опций редакторам ->
  `lsp_hub.registry_set_project_overlay`; при старте — в `setup_views()`
  до restore сессии, в рантайме — в `open_project()` до эмита корня;
- двухслойность владельцев настроек (главное правило: правки слоя приложения
  НЕ «пробивают» активное проектное перекрытие, а запись настроек приложения
  не «утаскивает» проектные значения в QSettings): тема — `theme_app_name` +
  хуки `handle_theme_switch` (комбик в углу при активном перекрытии правит
  слой проекта — маршрутизация по источнику текущего значения) и
  `theme_on_app_changed` (IDE пропускает apply, пока тему держит проект),
  `theme_settings_write` пишет `theme_app_name`; редактор —
  `DocumentManager.editor_ux_options()` (effective: QSettings +
  `merge_editor_ux_options` секции `editor`) vs `editor_ux_options_app()`;
  LSP — `LspHub._registry_app_json/_registry_project_json` +
  `_effective_registry` (проект > приложение > дефолт), `registry_app_json()`
  для страницы, `registry_apply_json/reset` трогают только слой приложения;
- страницы настроек грузят СЛОЙ ПРИЛОЖЕНИЯ (`theme_app_name`
  / `editor_ux_options_app` / `registry_app_json`), чтобы «Применить» не
  копировал перекрытие проекта в QSettings (с 9.6B2 это поведение —
  область «Приложение», у scope-aware страниц появилась и проектная область).

**Приёмка:** `tests/test_project_settings.py` (21: 10 юнитов project_settings
+ 2 юнита coerce/merge editor_options + 9 offscreen — старт с персистнутым
корнем и всеми тремя секциями оверлея, цикл тема open/close с откатом,
непробиваемость перекрытия + изоляция QSettings, маршрутизация комбика по
источнику, приоритеты/битые значения/runtime-правка опций редактора,
страница «Редактор» грузит слой приложения, слои LSP-реестра без утечек,
валидность API (нет проекта/секции/ключа/значения) + сигнал + копия get).

#### 9.6 (часть B2) — селектор области «Приложение/Проект» — ГОТОВО
- ядро остаётся про проекту-агностичным: `SettingsControl.scope_register(
  key, label, is_available)` + `scope_options()` (ленивая проверка
  доступности) + `scopes_refresh()`; область `'app'` («Приложение»)
  регистрирует само ядро, `'project'` («Проект») — `ProjectControl.setup`
  (доступна с открытым проектом), пересборка селектора — на
  `project_root_changed`; фасады `settings_scope_register/
  settings_scopes_refresh` на BaseMainWindow;
- `SettingsPage`: `scope_applicable` (окно показывает строку «Область»
  только для таких страниц), `scope`/`scope_set()` (по умолчанию `'app'`,
  константа `SCOPE_APP` в `settings_page.py`, `SCOPE_PROJECT` — в
  `project_control.py`); страница в `load/apply/reset_defaults` сама
  выбирает слой по `self.scope`;
- `SettingsWindow`: строка «Область: [QComboBox]» над стеком страниц,
  `_sync_scope_row` при выборе страницы/смене доступности; если текущая
  область страницы стала недоступна (проект закрыли при открытом окне) —
  откат на `'app'` с `page.load()`; программная пересборка комбика
  защищена флагом `_scope_syncing` (не грузит страницу повторно);
- страницы стали scope-aware на своих местах (та же клавиша/порядок):
  «Редактор» в проектной области грузит ЭФФЕКТИВные значения, «Применить»
  пишет весь набор ключей в секцию `editor` оверлея (пакетный
  `ProjectControl.project_settings_update` — валидация всех ключей до
  записи, одна запись файла, один сигнал), «Сбросить» снимает секцию;
  «LSP» — то же для `lsp.registry_json` (load = эффективный реестр);
  core-страницу «Внешний вид» в IDE заменил `AppearanceScopePage`
  (`main_wnd/appearance_scope_page.py`, та же key/order): в проектной
  области без перекрытия показывается тема приложения, apply — оверлей
  `appearance.theme_name`, reset — снимает оверлей (а не пишет «Светлая»);
- console-API дополнен: `project_settings_update` (в `ProjectControl`,
  не в console-списке — тот же `project_settings_set` покрывает одиночные
  ключи).

**Приёмка:** `tests/test_settings_scope.py` (9 offscreen — реестр областей
и ленивая доступность + отказ дубликата, видимость строки «Область» только
у scope-страниц (plain-страница прячет) + автопересборка селектора на смене
корня, редактор: load 14/20 по областям, пакетный apply в оверлей при целом
слое приложения + broadcast в фейк-редактор, reset снимает секцию; откат
области при закрытии проекта с перезагрузкой значений; замена страницы
темы на scope-aware + apply/reset темы через оверлей при целостном
`theme_app_name`; LSP load/apply/reset по слоям; `project_settings_update`
— либо все ключи либо ничего, один сигнал, снятие ключа `None`).

#### 9.7 — закладки: Monaco decorations + `.pyqtp/bookmarks.json` + панель — ГОТОВО
- чистый `controls/bookmark_control/bookmarks.py`: формат
  `<root>/.pyqtp/bookmarks.json` = `{"bookmarks": [{"path","line"}]}`
  (сосед `session.json`/`settings.json`), недоедливый разбор (битый JSON /
  мусор в записях — пропускаем), каноническая сортировка по (path, line) и
  дедупликация на записи, атомарная запись (`tempfile.mkstemp` +
  `Path.replace`), пустой набор снимает файл и пустой `.pyqtp/` (соседей не
  трогает); `filter_bookmarks` — срежка записей пропавших файлов (возвращает
  «живые» + «пропущенные»), `toggle_bookmark`/`bookmarks_for`/
  `next_bookmark_line` (переходы next/prev с циклом);
- `BookmarkControl(BaseControl)` (`controls/bookmark_control/
  bookmark_control.py`, регистрируется в `init_controls` после
  `ProjectControl`): единственный владелец состояния — пишет файл сразу при
  каждом изменении (`toggle_at`/`remove_at`/`clear_project_bookmarks`,
  отказы: нет проекта / путь вне корня / файла нет / сбой записи), при смене
  корня грузит снимок и срежет мёртвые файлы сразу в файл; экшены и
  меню «Закладки» (priority=-7): «Поставить/снять закладку» (Ctrl+Alt+L),
  «Следующая закладка» (Ctrl+Alt+K), «Предыдущая закладка» (Ctrl+Alt+J) —
  связки как в VS Code; экшены живут вместе с проектом; переходы next/prev —
  по закладкам активного файла, строка каретки берётся из python-зеркала
  `current_cursor`, каретка ставится через `set_cursor(...,
  move_to_position='center')`; console-API `bookmarks_show`; логгер
  `bcad.book` (группа «Проект»);
- панель `BookmarksView` (`views/bookmarks_view.py`, справа): пассивная —
  только `set_bookmarks(root, items)` от контрола; дерево файл→«Строка N»,
  счётчик «Закладок: N» / «Нет закладок» / «Проект не открыт»; двойной клик
  → `open_definition_uri` (тот же маршрут, что LSP-переходы), Delete и
  контекстное меню «Удалить закладку»/«Удалить все закладки» — намерениями
  на контрол;
- редактор: vendored-patch Monaco (`main.ts` + `style.css`) — свой набор
  decorations `bookmarkCollection` (не смешивать с `decorationsCollection`
  из `highlight_lines`), метка-глиф в дорожке + подсветка строки; сообщение
  моста `set_bookmarks` (набор строк файла, буферизуется коннектором),
  переприменение при смене модели/редактора и в `set_text`, отсечка строк за
  концом текста; клик по дорожке закладок (`GUTTER_LINE_DECORATIONS`) →
  `_toggle_bookmark_request {line}` → сигнал `bookmark_toggle_requested`
  `QtMonacoEditorWidget`, проводка на `toggle_at` — на стороне контрола
  (`_wire_doc_view` на `document_opened`, флаг `_pyqtp_bookmark_wired` на
  вьюхе — одно подключение на редактор); плюс vendored-patch зеркала
  каретки: Monaco шлёт `_current_cursor` на каждое
  `onDidChangeCursorPosition` (апстрим обновлял только по echo `set_cursor`
  — без этого toggle/next/prev не видели реальную каретку).

**Ограничения v1 (приняты как данность):** закладка привязана к номеру
строки и не «едет» за текстовыми правками; при переименовании файла (9.5)
не переезжает — запись с пропа́вшего файла снимается фильтром при следующем
открытии проекта. Порядок при смене корня: сессия проекта (9.6) поднимает
вкладки раньше закладок, финальный набор строк доносит broadcast контрола.

**Приёмка:** `tests/test_bookmarks.py` — 9 юнитов чистого модуля без GUI
(толерантный разбор, сортировка/дедуп на записи, атомарность без временных
файлов, снятие пустого файла с соседями, срежка мёртвых файлов,
переключение/переходы с циклом) + 12 offscreen-GUI (фейк-редактор, без
WebEngine): живость экшенов вместе с проектом и связки VS Code, загрузка
снимка при открытии + срезка мёртвых в файл, раздача строк только своего
файла открытым документам, смена проекта (свой снимок, файл старого цел),
закрытие проекта (файл остаётся), gating отказов `toggle_at`, переключение
сигналом дорожки (файл+панель+оба редактора, нет двойной проводки),
toggle_active по зеркалу каретки, next/prev с циклом, пустая активная
вкладка — молча, панель: переход/ Delete / «Удалить все» + 4 webengine
(глифы `.pyqtp-bookmark-glyph` в DOM, строка за текстом игнорируется,
набор переживает смену модели и короткий файл, каретка из JS прилетает в
`current_cursor`, `_toggle_bookmark_request` → сигнал с отсевом мусора).
Остаток этапа 9 закрыт; брейкпоинты закрыты этапом 11.1 (DAP).

### Этап 10 — Problems-панель, статус-бар, монитор LSP
- агрегация маркеров всех документов (модель маркеров Monaco читается из
  Python) → Problems-панель (дерево файл→проблемы, двойной клик → переход);
- статус-бар: счётчики ошибок/предупреждений, индикатор инициализации LSP;
- F8-навигация по проблемам; фильтр по текущему файлу;
- «Монитор LSP»: ring-buffer запросов/ответов в LspHub + панель с фильтрами
  (копируем сильную идею iide).
**Приёмка:** тесты агрегатора; live-проверка.

#### 10.1 — панель «Проблемы»: python-стор диагностик + нижняя панель — ГОТОВО

Агрегация — на python-стороне хаба, а НЕ чтением модели маркеров Monaco из
JS (первоначальная идея пункта): хаб и так видит весь трафик
`publishDiagnostics`, не нужен вендорный TS-патч и пересборка `js_build`, и
проблемы живут даже для закрытых документов (и для документов, где редактор
ещё не поднялся).

- Чистый модуль `controls/problems_control/problems.py` (без Qt): frozen
  `Problem` (координаты Monaco 1-based: LSP 0-based +1; `lsp_header` в живом
  IDE не вызывается — `headerLines` всегда 0), толерантный
  `diagnostics_from_publish` (мусорные записи — мимо; отсутствие `severity`
  = Ошибка по спецификации; `code` integer -> строка; не-файловые uri —
  «пустая публикация»), `file_path_from_uri`, счётчики серьёзности и
  подписи («Ошибок: 2, …», «12:5 Ошибка: текст (код)», `code` в подписи
  приоритетнее `source`).
- Стор в `LspHub`: `dict[(uri, server_name)] -> tuple[Problem, ...]` под
  замком + `add_diagnostics_observer`/`remove_diagnostics_observer` +
  `diagnostics_snapshot()` (uri -> слитые проблемы всех серверов,
  отсортированы). Сервер пишет через `DiagHooks` (publish/purge) из своего
  потока event-loop; пустой список снимает вклад этого сервера, смерть
  процесса — весь его вклад, `shutdown()` — весь стор (с оповещением).
  `owner` проставляется каждой проблеме при записи в стор.
- `ProblemsControl` (в `init_controls` после `bookmark_ctrl`): queued-мост
  `_DiagSignals` из потока хаба в GUI, обновление панели на снимки хаба и на
  `project_root_changed`; панель `views/problems_view.py`
  (key `ProblemsControl_problems_view`, `widget_area='bottom'`):
  QTreeWidget «файл -> проблемы» (порядок файлов по пути, внутри — по
  строке/колонке; относительные пути внутри проекта, как в панели
  закладок), двойной клик/Enter по проблеме -> `open_definition_uri`
  (тот же путь перехода, что LSP-переходы этапа 6), строка итога
  «Проблем: N (Ошибок: …)». Console-API `problems_show`. Экшенов и F8-
  навигации пока нет — это 10.2 (вместе со статус-баром).

**Приёмка:** `tests/test_lsp_hub_store.py` — 5 юнитов чистого модуля
(координаты/сортировка/отсев мусора, дефолт severity, code int -> str,
uri-пути) + 5 юнитов stора (мердж вкладов двух серверов в один uri и
замена вклада повторной публикацией, снятие ключа пустой публикацией и
чужих ключей, подписчики/дубликаты/снятие, протухание вклада мёртвого
сервера, очистка при `shutdown`) + 3 live (детерминированный фейковый
LSP-сервер `python <скрипт>` через реестр: реальный `publishDiagnostics`
через ws доходит до stора с 1-based координатами, пустая публикация
«чистого» файла не заводит ключ, `shutdown` гасит стор; плюс настоящий
basedpyright, skipif без бинаря). `tests/test_problems_panel.py` — 8
offscreen-GUI (живой `IdeMainWindow`+KDD+`LspHub`+`ProblemsControl`, фейк-
документы из харнеса 9.7): регистрация панели внизу, пустой старт
(«Нет проблем»), queued-обновление дерева из stора (два файла, два
владельца, сортировка, строка итога), относительные пути с открытым
проектом, переход двойным «кликом» (`open_definition_uri` -> фейковый
редактор, `(5, 4, 'center')`), пустая публикация убирает файл, остановка
хаба чистит панель, `problems_show` в Python-консоли.

#### 10.2 — статус-бар, F8-навигация, фильтр по текущему файлу — ГОТОВО

- Второй стор в `LspHub` — состояния LSP-процессов (индикатор статус-бара):
  `dict[(команда, root_uri)] -> 'запуск'|'готов'` (`SERVER_STATE_STARTING`/
  `SERVER_STATE_READY`) под своим замком; публичные
  `add/remove_server_state_observer` + `server_states()`. Ведётся в
  `_ensure_instance`: новый инстанс — «запуск» до хендшейка и «готов» после
  (ветка переиспользования/параллелного ожидания тоже выставляет «готов» —
  закрывает гонку холодного старта индикатора); сбой старта снимает запись;
  смерть процесса — снимает `DiagHooks.state_gone` (третье поле хуков,
  вместо отдельного параметра инстанса — лимит аргументов),
  `_kill_instances`/`shutdown` гасят весь стор.
- Статус-бар справа (`addPermanentWidget`, виджеты в
  `controls/problems_control/status_bar_widgets.py`, текстовые символы —
  темобезопасно и проверяемо программно без vision):
  `ProblemsCountWidget` — кликабельная кнопка «✕ N ⚠ M» (нули всегда,
  тултип «Проблем: N (Ошибок: …)», выключена при нуле, клик =
  `ensure_visible` панели; счётчики ВСЕГДА по полному набору — фильтр
  панели на них не влияет) и `LspInitWidget` — «LSP: инициализация… (a/b)»
  + busy-прогресс, виден только пока есть процесс в «запуск».
- F8/Shift+F8 — `QAction` (не обёртка `Action`… та форсит ApplicationShortcut;
  контекст WindowShortcut: в вебвью Monaco свои связки) + меню «Проблемы»
  (PriorityMenu priority=-6, между «Закладками» и «Вид»). Цикл — чистый
  `next_problem(entries, current, *, forward)` в `problems.py` по точкам
  `(путь, строка, колонка)` (тот же порядок, что у дерева панели): вперёд —
  первая строго после каретки, иначе цикл в начало; назад — последняя
  строго перед, на точном совпадении — предыдущая точка (не к себе), за
  началом — цикл в конец. Каретка — из python-зеркала `current_cursor`
  (как у закладок 9.7), переход — `open_definition_uri` + `Path.as_uri()`.
- Фильтр «Только текущий файл» — чекбокс в шапке `ProblemsView`
  (`set_current_document`/`is_current_file_only`/`current_file_only_changed`);
  вьюха держит полный снимок и перестраивает дерево, фильтр без активной
  вкладки никого не показывает — тот же набор использует F8
  (`problem_entries()`). Активная вкладка: новый сигнал
  `DocumentManager.document_activated` (эмитится только при реальной смене
  ключа в `_note_activated`). Гочка, найденная тестами: в момент эмита
  Qt-фокус ещё в прошлой вкладке (слот подъёма фокуса KDD висит ПОСЛЕ
  нашего слота на `ensure_visible_requested`) — панель и экшены берут ключ прямо
  из сигнала, а не через `active_document_get()`; закрытие вкладки —
  переснимок через `QTimer.singleShot(0)` (сосед активируется следующим
  сигналом сам).

**Демонстрация:** `tests/test_problems_status_nav.py` — 2 юнита без GUI
(цикл `next_problem`: промахи каретки/точные точки/выходы за границы; стор
состояний голого `LspHub`: запись/снятие/гейт дублей/очистка/подписчики) +
6 offscreen-GUI (счётчики текст/тултип/enabled, клик открывает панель,
индикатор LSP по `_set_server_state`/`_drop_server_state` через queued-мост,
F8-цикл через два файла с зеркалом каретки, фильтр меняет набор F8 и
доступность экшенов (счётчики статуса — нет), порядок `document_activated`
при открытии/повторах/закрытии). Правки затронули `document_manager` —
полный дефолтный прогон (203) и live/webengine (41) зелёные.

#### 10.3 — «Монитор LSP»: кольцо трафика хаба + панель с фильтрами — ГОТОВО

- Чистый модуль `controls/lsp_control/lsp_monitor.py` (только stdlib, без Qt и
  websockets — пишется из потока event-loop хаба под замком, читается из GUI):
  frozen `TrafficEvent` (сырое событие) -> `TrafficRecord` (в кольце: seq+ts),
  `TrafficRing` (ёмкость `RING_CAPACITY=1000`, вытеснение старых, нумерация
  `seq` продолжается и после `clear()` — id записей уникальны на всё время
  жизни), сводки `summarize_params`/`summarize_error`/`summarize_result`
  (срезка `SUMMARY_MAX_CHARS=160`; **текст документа не дампит никогда**:
  didOpen/didChange/willSave/didSave -> только `uri vN`), фабрики событий по
  точкам (`ev_request`/`ev_notification_to`/`ev_from_server`/
  `ev_hub_reply_to_server`/`ev_hub_reply_to_client`/`ev_client_request`/
  `ev_hub_error`), `MonitorFilter` + `filter_records`, `format_time`
  (ЧЧ:ММ:СС.дсс).
- Направления: `DIRECTION_TO` «→» (хаб -> процесс, реальный провод),
  `DIRECTION_FROM` «←» (процесс -> хаб), `DIRECTION_HUB` «↔» (сообщение
  породил сам хаб, процесс его не видел: запрос клиента, обслуженный хабом,
  ответ клиенту, ошибка хаба). Типы (9): запрос / запрос хаба / запрос
  сервера / запрос клиента / ответ / **ответ хаба** / ошибка / нотификация /
  нотификация сервера. Ответ хаба на запрос СЕРВЕРА — направление «→», бо
  уходит процессу на провод; ответ хаба КЛИЕНТУ — «↔».
- Точки записи в `lsp_hub` (пишется ровно то, что было на проводе):
  `_route_from_client` — id в строке уже ПЕРЕПИСАННЫЙ (`c{conn}:{id}`), как он
  и лёг в процесс; проглоченные клиентские нотификации (`initialized`, `exit`,
  `$/setTrace`) НЕ пишутся — сервер их не получил; `$/cancelRequest` — со
  сводкой уже переписанных params; `_handle_server_notification` — строка ДО
  разбора (проглоченные `window/logMessage`/`$/progress` процесс реально
  присылал, они видны); `_handle_server_request` — пара «← запрос сервера» +
  «→ ответ хаба»; `_handle_server_response` — строка «←» подписывается методом
  из таблицы pending (в ответе метода нет), `pending` расширен до
  `(conn, original_id, method)`; локальные id `hub{N}` там пропускаются — их
  пишет сам `request_local` парой «→ запрос хаба» (при отправке) + «← ответ
  хаба» (по приходе); `_on_process_dead` — ошибка хаба (−32000) на каждый
  висящий запрос; `_detach` и `stop()` — принудительные didClose и
  shutdown/exit процесса; `_start_inner` — `initialized`.
- Traffic-хук — 4-е поле `DiagHooks` (`traffic`), а не новый параметр
  `_ServerInstance` (лимит ruff PLR0913). API хаба:
  `add/remove_monitor_observer` (колбэк ИЗ потока event-loop — GUI-стороне
  свой queued-мост), `monitor_records()`, `monitor_total_seen()`,
  `monitor_clear()`, `live_server_names()` (комбик серверов). Кольцо ЖИВЁТ ПОСЛЕ
  `shutdown()` хаба — историю разбираемой сессии можно разобрать post-mortem;
  снимается только `monitor_clear()`.
- Контрол `LspMonitorControl` (`controls/lsp_control/lsp_monitor_control.py`,
  в `init_controls` после `problems_ctrl`): queued-мост `_MonitorSignals` +
  КОАЛЕСЦИРУЮЩИЙ сброс панели (`QTimer.singleShot(_FLUSH_MS=200)` и флаг
  `_pending_refresh`) — шквал completion/hover не гоняет `QTreeWidget` на
  каждую строку; меню «LSP» (PriorityMenu priority=-4, после «Вид»),
  console-API `lsp_monitor_records`/`lsp_monitor_open`, логгер `bcad.mon`
  (группа «LSP»).
- Панель `views/lsp_monitor_view.py` (key `LspMonitorControl_monitor_view`,
  `widget_area='bottom'`, пассивная — паттерн 9.x/10.x): 7 колонок
  Время/Сервер/Направление/Тип/Метод/Id/Детали, строка итога «Записей: N из R
  (всего было T)», фильтры — текст + комбики сервер/направление/тип + чекбокс
  нотификаций (по умолчанию ВЫКЛ: нотификации — шум), «Пауза» (снимки
  копятся, дерево заморожено — разобрать конкретный момент шквала), «Очистить»
  (локальная картинка гасится сразу, историю кольца снимает хаб по сигналу
  `clear_requested` — он её единственный владелец), умный автоскролл (вниз
  только если ползунок уже был у конца).
- Два решения продиктованы семантикой фильтров: явный выбор «нотификация» в
  комбике типа ПЕРЕКРЫВАЕТ галку нотификаций (иначе пункт комбика не работал
  бы никогда), и ответы на локальные запросы хаба помечаются «ответ хаба»
  (`ev_from_server(..., kind=...)`), иначе в панели не отличить ответ
  процессу от ответа процессом хабy.

**Демонстрация:** `tests/test_lsp_monitor.py` — 15 юнитов без GUI (кольцо:
продолжение `seq` после очистки, вытеснение по ёмкости, потокобезопасность
4×200 записей; сводки didOpen/обрезка/ошибки/результаты; фабрики направлений
и типов; `filter_records`; API кольца на голом `LspHub` — наблюдатели/очистка/
жизнь после `shutdown`; точки записи на `_ServerInstance` с фейковыми
процессом/сокетом: handshake-пары «↔», переписанный id в строке, сводка
didOpen без текста, проглоченные не пишутся, `$/cancelRequest`, подпись метода
в ответе, смерть процесса -> ошибки хаба, пара `request_local` (`hub1`),
`_detach` didClose, `live_server_names`) + 6 offscreen-GUI (панель
зарегистрирована, пустой старт, кольцо -> queued-мост -> коалесцированный
сброс в дерево, все фильтры, «Пауза», «Очистить» + console-API) + 2 live
(реальный ws-раунд с детерминированным фейковым сервером: handshake/didOpen/
publishDiagnostics в кольце и сводка без текста документа; отключение
редактора и остановка хаба — история доступна post-mortem). Итог: дефолтный
прогон 226, live/webengine 43 — зелёные.


### Этап 11 — отладчик DAP 🔶 (D1 готово; D2/D3 — впереди)

#### Этап 11.1 (D1) — DAP-клиент, брейкпоинты Monaco, консоль отладки ✅ (готово)

Топология: **stdio** — адаптер `python -m debugpy.adapter` БЕЗ `--port`
говорит DAP на собственном stdin/stdout (Content-Length-фрейминг; ноль
токенов/сетевых портов), debuggee адаптер поднимает сам после `launch`+
`configurationDone`. Ответ на `launch` приходит только после
`configurationDone` — поэтому обязателен отдельный читатель+``futures`` по
`request_seq` (`dap_session.DapSession`).

- Чистые модули `controls/dap_control/` (этап в стиле 9.x/10.x):
  `dap_framing` (фреймы/конструкторы сообщений), `dap_session` (клиент:
  ритуал initialize→launch→initialized→setBreakpoints→configurationDone→
  ответ launch, хук `on_breakpoints_ready`, `_HANDSHAKE_TIMEOUT`,
  `_fail_all` при смерти адаптера, `close(terminate=)` идемпотентен с
  закрытием транспорта пока loop жив — иначе unraisable `__del__`),
  `launch_config` (`.pyqtp/launch.json` — подмножество формата VS Code:
  name/program/args/cwd/env/stopOnEntry/justMyCode, `${workspaceFolder}`,
  первая конфигурация = цель запуска, дефолт — активный документ + cwd=
  корень; аргументы `launch`: `console:'internalConsole'` — нашей
  капбилити `supportsRunInTerminalRequest` нет), `breakpoints`
  (`.pyqtp/breakpoints.json` — тот же контракт, что у закладок 9.7),
  `dap_process` (`adapter_command()`, env-шов `PYQTP_DAP_ADAPTER_COMMAND`
  для тестового фейкового адаптера).
- `DapControl` (`dap_ctrl`, в `init_controls` после `problems_ctrl`):
  ленивый asyncio-loop-поток, сессия трогается только из loop-потока
  (`run_coroutine_threadsafe`), queued-мост `_DapSignals` + гейт поколения
  `_gen`; стат-машина IDLE/RUNNING/STOPPED; у `stopped` НЕТ строки — кадр
  берётся из `stackTrace` frame0 и вскрывается тем же маршрутом, что LSP-
  переходы (`open_definition_uri` + каретка с центрированием); консольные
  строки «[Отладка] …» (запуск/остановка/вывод debuggee/коды завершения);
  меню «Отладка» (PriorityMenu priority=-3, экшены 1..6), связки VS Code
  F5/Shift+F5/F9/F10/F11/Shift+F11 (обычные QAction WindowShortcut);
  console-API `debug_start/stop/continue/step_*/status/breakpoints_show`;
  логгер `bcad.dbg` (группа «Отладка»).
- Брейкпоинты: F9 на строке каретки (python-зеркало `current_cursor`,
  как 9.7/10.2); клик по дорожке — глиф брейкпоинта → переключение
  брейкпоинта, иначе (пусто/закладка) → закладка 9.7 сохранена; новый
  брейкпоинт — только F9. Раздача строк редакторам + широковещательный
  `setBreakpoints` в живую сессию при правках; стартовый набор уходит в
  handshake (хук `on_breakpoints_ready`).
- Monaco (вендорные патчи `main.ts`/`style.css`): отдельные
  `breakpointCollection`/`execLineCollection`, `routeGutterClick` по классу
  задетого глифа, bridge-случаи `set_breakpoints`/`set_execution_line`,
  тестовый хук `window.qtmonaco.__pyqtp_gutter_click`; CSS
  `.pyqtp-breakpoint-glyph/-line`, `.pyqtp-exec-gutter/-line`; пересборка
  `js_build` обязательна. Подсветка текущей строки останова чистится при
  конце сессии.
- Консоль: пассивная `views/debug_console_view.py` (`widget_area='bottom'`):
  read-only QPlainTextEdit (моноширинный, потолок буфера), строка статуса
  «Не отлаживаем/Выполняется/Остановлено», кнопка «Очистить»
  (`clear_requested`).
- Live-сессия в тестах: фейковый stdio DAP-адаптер (скрипт только stdlib,
  режимы normal/no_initialized/launch_fail/crash/dead/no_frames +
  JSONL-журнал запросов) через env-шов; живые прогоны — настоящий
  debugpy (entry-шаги 1→2→3 + print в консоль; брейкпоинт в цикле).

**Демонстрация:** `tests/test_dap_debugger.py` — 41 тест (36 дефолтных:
юниты фрейминга/launch.json/брейкпоинтов/сессии + offscreen-GUI стат-
машина/брейкпоинты/консоль; 2 live debugpy; 3 webengine DOM-пробы
глифов/строки останова/маршрута дорожки). Итог: дефолтный прогон 262,
live/webengine 48 — зелёные.

Остаток этапа: D2 — панели переменных/call stack/потоки + тулбар;
D3 — шаги по инструкциям, conditional брейкпоинты, hover значения,
вкладка Debug в настройках.

### Этап 11 plan (исторично, для D2/D3)
- `DapControl(BaseControl)` + DAP-клиент поверх нашего asyncio-опыта
  (Content-Length-фрейминг переиспользуется из LSP-обёртки);
- debugpy built-in + `.pyqtp/launch.json`;
- брейкпоинты Monaco decorations (клик по gutter) + персист, current-line;
- тулбар start/continue/stop/step-* и панели console/variables/call stack;
- **полноценные акселераторы F5/F10/F11** (в iide их нет — делаем сразу лучше).
Декомпозиция: D1 клиент+брейкпоинты, D2 панели, D3 шлифовка. Самыйёмкий этап.

### Этап 12 — скриптуемая IDE и CLI (наше уникальное преимущество)
- расширение IDE API в Python-консоли (`register_main_wnd_api`): открыть/закрыть
  документ, LSP-запрос от имени пользователя, exec JS в редакторе, навигация, поиск;
- CLI `ide.py`: позиционные файлы/папки (через D-Bus single instance),
  `-p/--project`, `--new-window`;
- welcome-диалог + недавние проекты.
В iide плагинов/скриптинга нет вовсе; у нас плагины и консоль уже в архитектуре.
Допустимо вставить раньше этапа 11.

### Этап 13 (задел) — git
Нет ни у одного из приложений. Статусы в дереве (`git status --porcelain`),
diff через встроенный `monaco.editor.createDiffEditor` (вендорится в qtmonaco),
коммит-панель. Отложить до завершения 7–10.

## 4. Что сознательно НЕ переносим из iide
- Tree-sitter-слой целиком — его роль (подсветка/фолдинг/индент/крючки)
  закрывают Monaco + semanticTokens.
- Собственный fuzzy-движок поиска — заменяется ripgrep.
- Мёртвый код (LanguageConfig-конфиги серверов в коде, ClangTidyRunner,
  11 неиспользуемых грамматик) — реестр хаба живёт в JSON и настройках GUI.
- Ручной JSON-RPC-клиент на ~1900 строк — наш хаб функциональнее
  (мультиклиентский, пул процессов, общий для всех редакторов, живой реестр).

## 5. Метрики: стек vs нативный эталон

Методика (прогон 2026-11-08, один прогон на приложение, одинаковые 5
python-файлов (≈49 КБ) одного «проекта», реальный запуск под Hyprland, одна
машина (AMD, warm FS-кэш); стенд `/tmp/opencode/bench/`):
- **старт окна** — от `exec` до первого окна приложения (наблюдатель окон
  Hyprland по дельте клиентского списка);
- **полная готовность** — от `exec` до готовности 5-го редактора:
  PYQTP — `'READY'` Monaco в каждой странице (QWebChannel-проверка);
  iide — вкладки восстанавливаются из `.iide/workspace.json` сразу с окном,
  LSP-стеки инициализированы через ~1.5 с (по их лог-файлу);
- **память** — суммарный RSS дерева процессов (самый полный срез на 45–60 с),
  LSP-серверы засчитываются у обоих: обе IDE поднимают один и тот же стек
  `basedpyright + ruff` (node-процесс basedpyright разогретый — ~0.5 ГБ,
  ruff — ~55 МБ; в PYQTP venv-бинарники через `uv run`, в iide тот же venv
  добавлен в PATH для равенства).

| Метрика | PYQTP IDE (Monaco/QWebEngine) | iide (GtkSourceView) |
|---|---|---|
| Старт окна (exec → окно) | **~7.0 с** (≈4.6 с — импорты PySide6/qtmonaco, ≈2.4 с — инициализация окна) | **0.35 с** |
| 5 редакторов полностью готовы | **~9.9 с** | **≈1.5–2 с** (вкладки вместе с окном; LSP-рукопожатие ~1.5 с) |
| RSS дерева, 5 вкладок (прогрев завершён) | **2.09 ГБ / 12 процессов** | **0.73 ГБ / 5 процессов** |
| RSS только главного процесса | 0.39 ГБ | 0.16 ГБ |
| Состав дерева | main + QtWebEngine (GPU + ~7 рендер/вспомогательных) + node basedpyright + ruff | main + node basedpyright + ruff + bash |

Вывод: общий LSP-бэкенд у обоих одинаковый (~0.56 ГБ node+ruff), вся разница —
редакторный слой: 5 вкладок Monaco с их Chromium-процессами стоят ≈**1.1 ГБ**
(~220 МБ на вкладку), тогда как нативный GtkSourceView живёт внутри главного
процесса (главный iide с 5 вкладками — всего 160 МБ). Старт окна и готовность
в iide на порядок быстрее (нативный запуск + мгновенное восстановление сессии
против Python-импортов и webview-инициализации).

Для нашего класса задач (полноценная IDE на современном десктопе) это
**принятый trade-off** за Monaco (LSP-провайдеры бесплатно, semanticTokens,
дифф-редактор, встроенные фичи этапа 7). Направления смягчения, если
понадобится: переиспользование редакторов (пул вместо закрытия вкладок —
архитектура `MonacoDocumentView` это позволяет), ускорение старта
(lazy-инпорты — 4.6 с импортов это отдельная цель; окно показывать до
тяжёлых импортов). Решение не раньше этапа 9.

## 6. Риск-реестр
- Каждая правка TS в `qtmonaco` = пересборка `js_build` (~30 с) — держим правило.
- Порядок проверки не меняется: ruff → pyright (из `packages/`) → pytest
  `-m ''` → живая проверка.
- Этап 11 (DAP) — самый крупный; декомпозиция D1/D2/D3, при откате —
  IDE полностью работоспособна без отладчика.
- Метрики памяти: при ухудшении (много вкладок) — пул редакторов, затем
  (крайний случай) рассмотрится нативный fallback-редактор; решение не раньше
  этапа 9.
