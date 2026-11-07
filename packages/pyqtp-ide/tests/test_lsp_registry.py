"""Тесты настраиваемого реестра LSP-серверов (этап 3.2/3.4).

Перенос харнеса /tmp/opencode/probe_registry_32.py:
секции A, B, D, E — детерминированные; C — живые initialize-хендшейки
с реальными процессами серверов (маркер ``live``).
"""

import json
import shutil
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QSettings
from websockets.sync.client import connect

from pyqtp.controls.lsp_control.lsp_hub import LspHub
from pyqtp.controls.lsp_control.lsp_registry import LspRegistry


def cfg_with_ruff(
    disabled: bool = False,
    root_markers: list[str] | None = None,
    drop_bad: bool = False,
) -> str:
    """JSON-конфиг языка python с двумя серверами (рuff отключаемый)."""
    cfg: dict[str, Any] = {
        'python': {
            'extensions': ['.py', '.pyi'],
            'servers': [
                {
                    'name': 'basedpyright',
                    'command': ['basedpyright-langserver', '--stdio'],
                },
                {'name': 'ruff', 'command': ['ruff', 'server'],
                 **({'enabled': False} if disabled else {})},
            ],
        },
    }
    if root_markers is not None:
        cfg['python']['root_markers'] = root_markers
    if drop_bad:
        cfg['python']['servers'].append({'name': 'bad', 'no_command': True})
        cfg['totally_bad'] = ['не объект']
    return json.dumps(cfg)


def ws_initialize(url: str) -> dict:
    """Полный initialize-хендшейк хабу по ws-адресу (синхронный клиент)."""
    with connect(f'ws://{url}', open_timeout=10, close_timeout=2) as ws:
        ws.send(json.dumps({
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'initialize',
            'params': {'processId': None, 'rootUri': None, 'capabilities': {}},
        }))
        for _ in range(5):
            msg = json.loads(ws.recv(timeout=20))
            if msg.get('id') == 1:
                return msg
    return {}


def stack_names(hub: LspHub, uri: str) -> list[str]:
    return [entry['owner'] for entry in hub.lsp_stack_for(uri)]


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    """Временный 'проект' с маркером корня (pyproject.toml) и python-файлом."""
    project = tmp_path / 'proj'
    project.mkdir()
    (project / 'pyproject.toml').write_text('[project]\nname = "probe"\n', encoding='utf-8')
    (project / 'bad.py').write_text('x = 1\n', encoding='utf-8')
    return project


@pytest.fixture
def hub() -> Iterator[LspHub]:
    """LspHub без главного окна (реестрный API работает без жизненного цикла)."""
    instance = LspHub()
    yield instance
    instance.shutdown()


@pytest.fixture
def live_hub(hub: LspHub, proj: Path) -> LspHub:
    """Хаб с поднятым ws-сервером и поднятыми процессами серверов (live)."""
    if not (shutil.which('basedpyright-langserver') and shutil.which('ruff')):
        pytest.skip('basedpyright-langserver / ruff не найдены в PATH')
    hub.ensure_started()
    return hub


# --- A. чистый реестр ---


def test_default_python_stack(proj: Path) -> None:
    reg = LspRegistry.default()
    names = [s.name for s in reg.servers_for_uri((proj / 'bad.py').as_uri())]
    assert names == ['basedpyright', 'ruff']


def test_disabled_server_filtered(proj: Path) -> None:
    reg = LspRegistry.from_json(cfg_with_ruff(disabled=True))
    names = [s.name for s in reg.servers_for_uri((proj / 'bad.py').as_uri())]
    assert names == ['basedpyright']


def test_json_roundtrip() -> None:
    reg = LspRegistry.default()
    assert LspRegistry.from_json(reg.to_json()).to_dict() == reg.to_dict()


def test_broken_json_raises() -> None:
    with pytest.raises(ValueError):
        LspRegistry.from_json('{битый')


def test_broken_entries_skipped() -> None:
    reg = LspRegistry.from_json(cfg_with_ruff(drop_bad=True))
    assert list(reg.languages) == ['python']
    assert len(reg.languages['python'].servers) == 2


# --- B. API хаба: стеки, apply, reset ---


def test_hub_default_stack(hub: LspHub, proj: Path) -> None:
    assert stack_names(hub, (proj / 'bad.py').as_uri()) == ['basedpyright', 'ruff']


def test_hub_apply_and_reset(hub: LspHub, proj: Path) -> None:
    uri = (proj / 'bad.py').as_uri()
    hub.registry_apply_json(cfg_with_ruff(disabled=True))
    assert stack_names(hub, uri) == ['basedpyright']

    with pytest.raises(ValueError):
        hub.registry_apply_json('не json')
    # реестр не изменился после ошибки
    assert stack_names(hub, uri) == ['basedpyright']

    hub.registry_reset()
    assert stack_names(hub, uri) == ['basedpyright', 'ruff']


# --- C. живые хендшейки через хаб ---


@pytest.mark.live
def test_live_handshakes(live_hub: LspHub, proj: Path) -> None:
    uri = (proj / 'bad.py').as_uri()
    hosts = live_hub.ws_hosts_for(uri)
    ruff_url = next(h for h in hosts if h.endswith('/ruff'))
    bp_url = next(h for h in hosts if h.endswith('/basedpyright'))

    caps = (ws_initialize(ruff_url).get('result') or {}).get('capabilities') or {}
    assert caps.get('hoverProvider'), caps

    caps = (ws_initialize(bp_url).get('result') or {}).get('capabilities') or {}
    assert caps.get('completionProvider'), caps

    pool_cmds = {key[0] for key in live_hub._instances}
    assert pool_cmds == {'basedpyright-langserver --stdio', 'ruff server'}

    # отключённый сервер -> заглушка с пустыми capabilities
    live_hub.registry_apply_json(cfg_with_ruff(disabled=True))
    resp = ws_initialize(ruff_url)
    assert resp and (resp.get('result') or {}).get('capabilities') == {}
    live_hub.registry_reset()


# --- D. правило поиска корня проекта из конфига ---


def test_root_markers_rule(hub: LspHub, proj: Path) -> None:
    nested = proj / 'nested' / 'sub'
    nested.mkdir(parents=True)
    (nested / 'deep.py').write_text('x = 1\n', encoding='utf-8')
    (proj / 'nested' / '.custom_root').touch()
    deep_uri = (nested / 'deep.py').as_uri()

    assert hub._registry.root_uri_for(deep_uri) == proj.as_uri()

    hub.registry_apply_json(cfg_with_ruff(root_markers=['.custom_root']))
    assert hub._registry.root_uri_for(deep_uri) == (proj / 'nested').as_uri()


# --- E. чтение/запись реестра из QSettings ---


def test_settings_roundtrip(hub: LspHub, proj: Path, tmp_path: Path) -> None:
    ini = tmp_path / 'lsp_registry_settings.ini'
    settings = QSettings(str(ini), QSettings.Format.IniFormat)
    # заглушка главного окна: hubs-контролу нужен только атрибут settings
    fake_wnd: Any = types.SimpleNamespace(settings=settings)
    hub._main_wnd = fake_wnd

    hub.settings_write()
    assert not settings.value(f'{hub.key}/registry_json', '', str)

    settings.setValue(f'{hub.key}/registry_json', cfg_with_ruff(disabled=True))
    settings.sync()

    reader = LspHub()
    reader._main_wnd = fake_wnd
    reader.settings_read()
    assert stack_names(reader, (proj / 'bad.py').as_uri()) == ['basedpyright']

    # сброс на дефолт чистит ключ настроек
    reader.registry_reset()
    reader.settings_write()
    assert not settings.value(f'{hub.key}/registry_json', '', str)
    reader.shutdown()
