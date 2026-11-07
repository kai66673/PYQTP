from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QProcess
from PySide6.QtDBus import QDBusConnection

_session_bus_name = 'bcad_session_bus'
_test_session_bus_name = 'test_bcad_session_bus'


def _connect_to_bcad_dbus_session_win() -> QDBusConnection:
    dbus_address = 'tcp:host=localhost,port=54321,family=ipv4'

    def try_run_dbus_daemon() -> bool:
        dbus_exe_dir = Path(__file__).parent / 'dbus_win'
        dbus_process = QProcess()
        dbus_process.setWorkingDirectory(str(dbus_exe_dir))
        env = dbus_process.processEnvironment()
        env.insert('DBUS_SESSION_BUS_ADDRESS', dbus_address)
        dbus_process.setProcessEnvironment(env)
        conf_path = dbus_exe_dir / 'dbus-session.conf'
        ok, _pid = dbus_process.startDetached(
            'dbus-daemon.exe', arguments=[f'--config-file={conf_path!s}']
        )
        return ok

    os.environ['DBUS_SESSION_BUS_ADDRESS'] = dbus_address
    session_bus = QDBusConnection.connectToBus(
        dbus_address, _test_session_bus_name
    )
    if session_bus.isConnected():
        QDBusConnection.disconnectFromBus(_test_session_bus_name)
        return QDBusConnection.connectToBus(dbus_address, _session_bus_name)

    if not try_run_dbus_daemon():
        return session_bus

    return QDBusConnection.connectToBus(dbus_address, _session_bus_name)


def _connect_to_bcad_dbus_session_nix() -> QDBusConnection:
    return QDBusConnection.connectToBus(
        QDBusConnection.BusType.SessionBus, _session_bus_name
    )


def connect_to_bcad_dbus_session() -> QDBusConnection:
    return (
        _connect_to_bcad_dbus_session_win()
        if sys.platform == 'win32'
        else _connect_to_bcad_dbus_session_nix()
    )
