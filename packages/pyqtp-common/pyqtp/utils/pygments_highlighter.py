from __future__ import annotations

import contextlib
import sys
from typing import Any

from pygments import highlight
from pygments.formatter import Formatter
from pygments.lexers import *  # type:ignore[reportWildcardImportFromLibrary]
from PySide6.QtGui import (
    QColor,
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextDocument,
)


class QPygmentsFormatter(Formatter):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.data: list[QTextCharFormat] = []
        self.styles = {}
        for token, style in self.style:
            fmt = QTextCharFormat()

            if style["color"]:
                fmt.setForeground(QColor(f'#{style["color"]}'))
            if style["bgcolor"]:
                fmt.setBackground(QColor(f'#{style["bgcolor"]}'))
            if style["bold"]:
                fmt.setFontWeight(QFont.Weight.Bold)
            if style["italic"]:
                fmt.setFontItalic(True)
            if style["underline"]:
                fmt.setFontUnderline(True)
            self.styles[str(token)] = fmt

    def format(self, tokensource, outfile) -> None:
        self.data = []

        for ttype, value in tokensource:
            self.data.extend([self.styles[str(ttype)]] * len(value))


class PygmentsSyntaxHighlighter(QSyntaxHighlighter):
    def __init__(
        self, parent: QTextDocument, *,
        lang: str = "python", style: str = "solarized-dark"
    ) -> None:
        super().__init__(parent)

        self.formatter = QPygmentsFormatter(style=style)
        self.lexer = get_lexer_by_name(lang)

    def highlightBlock(self, text: str) -> None:
        text = self.document().toPlainText()
        if not text.endswith("\n"):
            text += "\n"

        highlight(text, self.lexer, self.formatter)

        block = self.currentBlock()
        pos = block.position()
        for i in range(len(text)):
            with contextlib.suppress(IndexError):
                self.setFormat(i, 1, self.formatter.data[pos + i])


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication, QPlainTextEdit
    app = QApplication(sys.argv)

    python = QPlainTextEdit()
    python.setWindowTitle("python")
    hl = PygmentsSyntaxHighlighter(python.document(), lang="python")
    python.show()

    sys.exit(app.exec_())
