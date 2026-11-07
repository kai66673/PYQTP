import fcntl
import os
import pty
import socket
import struct
import sys
import termios
import threading
from http.server import SimpleHTTPRequestHandler
from socketserver import TCPServer

from PySide6.QtCore import QObject, QSocketNotifier, Qt, QUrl, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QVBoxLayout,
    QWidget,
)

os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--log-level=3"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # noqa: PTH100, PTH120
ASSETS_DIR = os.path.join(BASE_DIR, "xterm_assets")  # noqa: PTH118

class LocalAssetsServer:
    def __init__(self, directory):
        self.directory = directory
        self.port = self.find_free_port()
        self.httpd = None
        self.thread = None

    def find_free_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', 0))
            return s.getsockname()[1]

    def start(self):
        class Handler(SimpleHTTPRequestHandler):
            def __init__(req, *args, **kwargs):  # noqa: N805  # pyright: ignore[reportSelfClsParameterName]
                super().__init__(*args, directory=self.directory, **kwargs)
            def log_message(self, format, *args):  # noqa: A002
                pass

        TCPServer.allow_reuse_address = True
        self.httpd = TCPServer(("127.0.0.1", self.port), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def get_url(self):
        return f"http://127.0.0.1:{self.port}/index.html"

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()


class TerminalBackend(QObject):
    def __init__(self, view):
        super().__init__()
        self.view = view
        self.fd = None
        self.pid = None
        self.notifier = None

    def start_shell(self):
        if self.pid is not None:
            return
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.environ["TERM"] = "xterm-256color"
            os.chdir(os.path.expanduser("~"))  # noqa: PTH111
            os.execv("/bin/zsh", ["/bin/zsh"])  # noqa: S606
        else:
            self.notifier = QSocketNotifier(self.fd, QSocketNotifier.Type.Read, self)
            self.notifier.activated.connect(self.handle_pty_read)

    @Slot(str)
    def receive_input(self, data):
        if self.fd:
            os.write(self.fd, data.encode('utf-8'))

    @Slot(int, int)
    def resize_pty(self, rows, cols):
        if self.fd:
            s = struct.pack('HHHH', rows, cols, 0, 0)
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, s)

    def handle_pty_read(self):
        if self.fd is None:
            return

        try:
            data = os.read(self.fd, 4096)
            if data:
                payload = data.decode('utf-8', errors='ignore')
                js_code = f"writeToTerminal({payload!r});"
                self.view.page().runJavaScript(js_code)
        except OSError:
            if self.notifier:
                self.notifier.setEnabled(False)


# ==============================================================================
# НАШ ОТДЕЛЬНЫЙ ПЕРЕИСПОЛЬЗУЕМЫЙ ВИДЖЕТ ТЕРМИНАЛА
# ==============================================================================
class XTermWidget(QWidget):
    """Полноценный инкапсулированный виджет терминала уровня VTE"""
    def __init__(self, parent=None):
        super().__init__(parent)

        # Настраиваем разметку самого виджета
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Запускаем персональный встроенный HTTP-сервер для этого виджета
        self.server = LocalAssetsServer(ASSETS_DIR)
        self.server.start()

        # Инстанцируем встроенный браузер
        self.browser = QWebEngineView(self)
        layout.addWidget(self.browser)

        page = self.browser.page()

        page.featurePermissionRequested.connect(
            lambda origin, feature: page.setFeaturePermission(
                origin,
                feature,
                QWebEnginePage.PermissionPolicy.PermissionGrantedByUser
            )
        )

        # Настраиваем мост связи Qt <-> JS
        self.channel = QWebChannel()
        self.backend = TerminalBackend(self.browser)
        self.channel.registerObject("backend", self.backend)
        self.browser.page().setWebChannel(self.channel)

        # Триггер старта шелла по окончании загрузки страницы
        self.browser.loadFinished.connect(self.on_page_loaded)

        # Загружаем локальный адрес
        self.browser.setUrl(QUrl(self.server.get_url()))

    def on_page_loaded(self, success):
        if success:
            self.backend.start_shell()

    def on_teardown(self):
        self.server.stop()


    def closeEvent(self, event):
        """Гарантированно тушим сервер при закрытии/удалении виджета"""
        self.server.stop()
        super().closeEvent(event)




if __name__ == "__main__":
    # ==============================================================================
    # ДЕМОНСТРАЦИЯ ИСПОЛЬЗОВАНИЯ В СЛОЖНОМ ИНТЕРФЕЙСЕ (В ТАБАХ)
    # ==============================================================================
    from PySide6.QtWidgets import (
        QApplication,
        QLabel,
        QMainWindow,
        QTabWidget,
    )

    class MainWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("PySide6 IDE-like Interface with VTE Terminals")
            self.resize(1000, 650)

            # Создаем менеджер вкладок, чтобы показать автономность виджетов
            self.tabs = QTabWidget(self)
            self.setCentralWidget(self.tabs)

            # Вкладка 1: Обычный текстовый виджет
            demo_label = QLabel('Тут может быть ваше основное приложение или редактор кода')
            demo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tabs.addTab(demo_label, "Главная")

            # Вкладка 2: Первый независимый терминал
            self.terminal_1 = XTermWidget(self)
            self.tabs.addTab(self.terminal_1, "Терминал Bash 1")

            # Вкладка 3: Второй абсолютно независимый терминал со своим процессом bash!
            self.terminal_2 = XTermWidget(self)
            self.tabs.addTab(self.terminal_2, "Терминал Bash 2")

    if not os.path.exists(os.path.join(ASSETS_DIR, "xterm.js")):  # noqa: PTH110, PTH118
        sys.exit(1)

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
