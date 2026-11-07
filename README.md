# PYQTP

Десктопное приложение на PySide6 (платформа + IDE): Monaco-редактор, LSP через
встроенный ws-хаб, доки (KDDockWidgets), система плагинов и страниц настроек.

## Быстрый старт (от клона до запуска)

```bash
# 0. Требования: установленный uv (https://docs.astral.sh/uv/) и node >= 20
#    (для пересборки js-фронтенда Monaco). Python 3.11 будет скачан uv сам.

# 1. Клон (вложенных submodule нет — qtmonaco внутри как squash-subtree)
git clone git@github.com:kai66673/PYQTP.git
cd PYQTP

# 2. Окружение workspace (НЕ обычный `uv sync` — он вычищает dev-группы)
uv sync --all-packages --all-groups

# 3. Пересборка js-фронтенда Monaco (31 МБ, в git не лежит)
cd packages/qtmonaco/backend
npm install && npm run build
mkdir -p ../qtmonaco/js_build && cp -r dist/* ../qtmonaco/js_build/
cd ../../..

# 4. Запуск IDE
cd packages/pyqtp-ide/pyqtp
uv run python ide.py -v
```

Дополнительно:

- демо-приложение core: `uv run python launcher.py` из `packages/pyqtp-core/pyqtp/`;
- если IDE уже запущен, новый экземпляр лишь активирует существующее окно
  (single-instance через D-Bus); обход — `BCAD_DISABLE_DBUS=1`;
- проверки кода, структура проекта и особенности запуска — в `AGENTS.md`,
  планы развития — в `docs/`.
