# План: система страниц настроек PYQTP

Цель: общая инфраструктура «окна настроек со страницами», где каждую страницу
поставляет свой контрол/плагин. После этого LSP-реестр (п. 3.4 из `docs/LSP_PLAN.md`)
станет просто «первой настоящей страницей», а не самодельным диалогом.

Статус: **этапы 1 и 2 реализованы и проверены** (ruff 10 / pyright 18 /
смоук `/tmp/opencode/smoke_settings.py` 10 проверок PASS / живой IDE без
traceback). Решения R1–R5 приняты в редакции ниже.

## Что уже есть (измерено по коду)

- Механизм ХРАНЕНИЯ настроек полный: `PluggableMainWindow.init_controls()` →
  launcher вызывает `settings_read_all()` → на закрытии `settings_write_all()`
  (`pluggable_main_window.py:283-307`). Каждый контрол/плагин сам читает/пишет свой
  ключ в `QSettings` (`org=PYQTP`, `app` — по приложению).
- Механизм РЕДАКТИРОВАНИЯ отсутствует: из настроек в UI меняются только тема
  (`ThemeSwitchingView` — док-вью с комбикомом) и включение плагинов (кнопки в
  док-вью `UserPluginsView`). Никаких диалогов/форм нигде нет.
- Стиль реестров в базе: `view_register/unregister` на самом окне
  (`pluggable_main_window.py:265+`, делегирование в `dock_views_manager`
  `base/docking_system/base_views_manager.py:24,72`; сортировка по `order`,
  автогенерация ключа из `type(view).__name__` — kdd-менеджер) и
  `events_log_register` (EventsControl) — новую систему делаем в том же стиле.
- Контролов в core всего пять: `api_control`, `events_control`, `help_control`,
  `plugins_control` (plugin/user_plugin), тема — это `base/theme_control.py`
  (не в каталоге controls). Каталога views_control нет.
- `PriorityMenu` из `pyqtp.utils` собирает меню/тулбары из строк вида
  `main_menu:file:99_name` (добавление экшенов в меню — его эксклюзив, вью к меню
  отношения не имеют).
- `PythonWidget` (редактор кода Monaco/веб) существует в core, но тяжёлый
  (QWebEngine) — для JSON-редактора не обязателен.

## Архитектура

### 1. `SettingsPage` — базовая страница (pyqtp-core, `pyqtp/controls/settings_control/`)

Лёгкий класс НЕ от BaseView и не от QWidget — страница-обёртка, которую создаёт
контрол-владелец:

```python
class SettingsPage:
    key: str                    # уникальный id ('lsp', 'theme', ...)
    title: str                  # отображаемое имя ('Языковые серверы (LSP)')
    order: float = 0            # порядок в списке страниц (как у вью)
    def create_page_widget(self) -> QWidget: ...   # виджет с содержимым
    def load(self) -> None: ...      # опц.: значения из настроек -> в виджет
    def apply(self) -> None: ...     # опц.: виджет -> QSettings + живое применение
    def reset_defaults(self) -> None: ...  # опц.: сброс страницы к дефолтам
```

Контракт:
- `create_page_widget()` вызывается **лениво** (при первом открытии страницы),
  чтобы значения всегда читались из актуальных настроек, а не на старте приложения;
- `apply()` может бросить `ValueError` с человекочитаемым текстом — окно покажет
  ошибку в статус-строке окна, настройки не испорчены;
- страницы, которым не нужен «Применить» (мгновенное применение, как у темы),
  просто не реализуют `apply`.

### 2. `SettingsControl(BaseControl)` (pyqtp-core, там же)

- Реестр: `page_register(page)` / `page_unregister(key)` + `page_get(key)` /
  `pages_sorted()` (сортировка по `order`, затем `key`), сигналы
  `page_registered/page_deregistered` (стиль `events_log_register`).
- Фасад в **`BaseMainWindow`** (не в `PluggableMainWindow` — контрол
  регистрируется там же): `settings_page_register(page)` /
  `settings_page_unregister(key)` — как `view_register`. Плюс переменные
  `settings_page_register/unregister/settings_window_show` в Python-консоли
  (`register_main_wnd_api`).
- Окно настроек (создаётся лениво, singleton на контрол):
  - `QDialog` **немодальное** (`show()`), заголовок «Настройки»;
  - слева `QListWidget` со страницами, справа `QStackedWidget`;
    сверху `QLineEdit` «Поиск настройки...» (фильтр по названию страницы);
  - внизу кнопки: **Применить** / **Откатить** (`load()`) /
    **Сбросить по умолчанию** — активны только если страница реализует
    соответствующий метод (`supports_apply/supports_reset`); **Закрыть**
    (закрытие окна — это скрытие с сохранением состояния, X-крестик и Esc
    тоже перехвачены);
  - ошибка `apply()` (`ValueError`/`TypeError`) показывается красным в
    статус-лейбле окна, без модальных попапов;
  - состояние окна (`<SettingsControl.key>/{geometry,splitter,current_page}`)
    сохраняется при скрытии.
- Вход в окно: экшен «Настройки приложения...» (Ctrl+,) подсаживается в уже
  существующее меню «Настройки» (его создаёт `UserPluginControl`, priority=9)
  на priority=10; отдельное меню «Сервис» не заводим.
- Сам контрол больше ничего не хранит, кроме состояния окна.

### 3. Демонстрационная страница в core: тема

Страница «Внешний вид» (`AppearanceSettingsPage`, key `appearance`, order 0):
комбо `ThemeManager.theme_names`, `apply()` → `theme_manager.switch_to(...)` +
синхронизация комбикоса в углу меню, `load()` → текущая тема,
`reset_defaults()` → «Светлая». Persistence — общим механизмом
`theme_settings_write` при закрытии приложения (страница сама ничего не пишет).
Это первый сквозной прогон контракта (create/load/apply/reset).
Существующее переключение темы (комбо в углу меню) остаётся как есть.

### 4. Страница LSP в pyqtp-ide (= закрытие п. 3.4)

`LspHub` в `setup()` регистрирует `LspRegistrySettingsPage(self)`
(key `lsp`, order 50) через `main_wnd.settings_page_register(...)`:
- виджет: моноширинный `QPlainTextEdit` с `registry_json()` + ярлык-справка
  о формате; отдельных кнопок на странице нет — кнопки окна
  («Применить» = `registry_apply_json(text)`, «Сбросить по умолчанию» =
  `registry_reset()`, «Откатить» = перечитать `registry_json()`);
- ошибка битого JSON ловится окном в статус-лейбл, действующий реестр цел
  (контракт `registry_apply_json` не менялся);
- чекбоксы `trace_workspace_folders*` на страницу не выносили (по желанию).

## Этапы работ

Этап 1 (core): `SettingsPage` + `SettingsControl` + `SettingsWindow` + фасады
`BaseMainWindow` + меню + состояние окна + эталонная страница темы.
Проверка: ruff (база 10), pyright (база 18), смоук
`/tmp/opencode/smoke_settings.py` (оффшорный `QApplication`, отдельный
пространство QSettings `PYQTP_SMOKE/PYQTP_SETTINGS_TEST`; 10 проверок:
реестр/дубликаты/кнопки/apply темы/откат/ошибка apply/reset/поиск/
deregister/состояние окна) — PASS.
Статус: **готово**.

Этап 2 (ide): `SettingsControl` приходит в IDE через `BaseMainWindow` (менять
`IdeMainWindow.init_controls` не пришлось) + страница LSP от `LspHub` в
`setup()` + регресс `probe_registry_32.py` (19/19 PASS — API реестра не
менялся; `setup()` с заглушкой main_wnd харнесом не вызывается) + перезапуск
живого IDE (новый hub-порт, в stdout нет traceback — регистрация страницы в
`setup()` прошла).
Статус: **готово**.

Этап 3 (отложено, не в объёме плана): иконки страниц, экспорт/импорт JSON
настроек, перенос переключения темы из угла меню в окно с удалением дубли.

## Решения (приняты)

- R1: инфраструктура живёт в **pyqtp-core** (IDE-специфичные страницы — в ide).
- R2: страница — отдельный класс-обёртка, **не BaseView** (вью — это доки, окно
  настроек — единая панель со стеком; смешение исказит обе системы).
- R3: окно **немодальное** (`QDialog.show()`), применение явной кнопкой
  «Применить» (кнопки активны только у страниц, реализующих метод).
- R4: контракт `apply()`/`reset_defaults()` кидает `ValueError`/`TypeError` →
  красный статус окна, записи нет.
- R5: страница темы в core как эталонная (демо + полезный бонус).
- R6 (уточнение по ходу): поиск по страницам сделан сразу (дешёвый фильтр по
  названию); вход в окно — в существующее меню «Настройки», нового меню нет.
