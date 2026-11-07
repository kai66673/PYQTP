import os

from PySide6.QtGui import QWheelEvent

from pyqtp.theme.theme import Theme

os.environ['QT_API'] = 'pyside6'

from PySide6.QtWidgets import QApplication
from qtconsole.inprocess import QtInProcessKernelManager
from qtconsole.rich_jupyter_widget import RichJupyterWidget

MINIMUM_FONT_SIZE = 3


class IPythonConsoleWidget(RichJupyterWidget):
    """
    Convenience class for a live IPython console widget.
    We can replace the standard banner using the custom_banner argument
    """

    def __init__(self, custom_banner=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if custom_banner:
            self.banner = custom_banner
        self.kernel_manager = kernel_manager = QtInProcessKernelManager()
        kernel_manager.start_kernel()
        assert kernel_manager.kernel is not None
        kernel_manager.kernel.gui = 'qt4'
        self.kernel_client = kernel_client = kernel_manager.client()
        kernel_client.start_channels()
        self.font_size = 10
        self.reset_font()

        def stop():
            kernel_client.stop_channels()
            kernel_manager.shutdown_kernel()
            QApplication.instance().exit()  # type:ignore[reportOptionalMemberAccess]

        self.exit_requested.connect(stop)  # type:ignore[reportAttributeAccessIssue]

    def set_theme(self, theme: Theme) -> None:
        self.set_default_style(colors='linux' if theme.is_dark else 'lightbg')

    def on_teardown(self):
        self.kernel_client.stop_channels()  # type:ignore[reportOptionalMemberAccess]
        self.kernel_manager.shutdown_kernel()  # type:ignore[reportOptionalMemberAccess]

    def push_variables(self, variable_dict):
        """Given a dictionary containing name / value pairs, push those variables to the IPython console widget"""
        self.kernel_manager.kernel.shell.push(variable_dict)  # type:ignore[reportOptionalMemberAccess]

    def clear_terminal(self):
        """Clears the terminal"""
        self._control.clear()  # type:ignore[reportOptionalMemberAccess]

    def print_text(self, text):
        """Prints some plain text to the console"""
        self._append_plain_text(text)

    def execute_command(self, command):
        """Execute a command in the frame of the console widget"""
        self._execute(command, hidden=False)

    def handle_wheel_with_ctrl(self, event: QWheelEvent):
        delta = event.angleDelta()
        event.ignore()
        if delta.y() == 0:
            return
        d = delta.y() / abs(delta.y())
        if d < 0.0:
            self.font_size += 1
            self.reset_font()
        elif self.font_size > MINIMUM_FONT_SIZE:
            self.font_size -= 1
            self.reset_font()
