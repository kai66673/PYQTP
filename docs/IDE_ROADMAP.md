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
| Регрессия: 25 pytest-тестов (7 live/webengine), ruff/pyright базы не меняются | работает |

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
| Отладчик DAP | есть (debugpy/lldb; **без F5/F10/F11**) | нет | **этап 11** |
| Tree-sitter (15 грамматик, 5 рабочих) | есть | не нужно — роль закрывают Monaco + semanticTokens | не переносим |
| Закладки/брейкпоинты в JSON проекта | есть | нет | этап 9 (закладки) / 11 (брейкпоинты) |
| Плагины/расширяемость | нет | **есть в архитектуре** (plugins, Python-консоль) | **этап 12** — наше преимущество |
| CLI, welcome-экран, недавние проекты | нет | нет | этап 12 |
| Git | нет у обоих | — | этап 13 (отложен) |
| Тесты | нет | **есть** (pytest + live/webengine-маркеры) | держим планку |

## 3. Этапы 7–13

### Этап 7 — «бесплатный» Monaco: встроенные фичи в UI
Всё уже есть в Monaco; нужно лишь включить/назначить:
find/replace (regex/case/word), folding, minimap, bracket pairs + автопары,
multi-cursor, expand selection (Ctrl+Shift+W), zoom, jump history Alt+←/→
(`goBack`/`goForward`).
Правки: `packages/qtmonaco/backend/src/main.ts` (editor options, команды) →
пересборка `js_build`. Опции — на страницу настроек «Внешний вид»/редактор.
**Приёмка:** webengine-тесты find/replace и folding; живая проверка.

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

### Этап 9 — проект и сессии
- панель дерева проекта (QTreeView, lazy, reveal активного файла, контекстное меню);
- «Открыть папку» + сессия проекта (вкладки/активная/раскладка; хранение —
  каталог `.pyqtp/` проекта или QSettings по пути);
- quick open (Ctrl+P): fuzzy по кэшу файлов;
- поиск по проекту (Ctrl+Shift+F): субпроцесс **ripgrep** (`--json`) →
  results-панель → клик = `open_definition_uri` + reveal (regex/case/include
  бесплатно с rg; собственный fuzzy-движок как в iide не пишем);
- файловые операции: create/rename (**LSP `willRenameFiles`/`didRenameFiles`**
  — уже реализованы в iide как эталон; у хаба пока нет, добавим сообщение
  DocumentManager→LspHub)/delete в корзину с подтверждением;
- закладки: Monaco decorations + `.pyqtp/bookmarks.json` + панель закладок.
**Приёмка:** юнит-тесты scorer'а и rg-парсера (без GUI); webengine-тест закладок.

### Этап 10 — Problems-панель, статус-бар, монитор LSP
- агрегация маркеров всех документов (модель маркеров Monaco читается из
  Python) → Problems-панель (дерево файл→проблемы, двойной клик → переход);
- статус-бар: счётчики ошибок/предупреждений, индикатор инициализации LSP;
- F8-навигация по проблемам; фильтр по текущему файлу;
- «Монитор LSP»: ring-buffer запросов/ответов в LspHub + панель с фильтрами
  (копируем сильную идею iide).
**Приёмка:** тесты агрегатора; live-проверка.

### Этап 11 — отладчик DAP
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
