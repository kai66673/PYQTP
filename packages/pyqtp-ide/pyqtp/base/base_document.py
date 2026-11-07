from __future__ import annotations

from typing import Protocol


class Document(Protocol):
    def identity(self) -> str:
        ...

    def is_modified(self) -> bool:
        ...

    def set_modified(self, modified: bool = True) -> None:  # noqa: FBT001, FBT002
        ...

    def do_save(self) -> bool:
        ...

    def save(self) -> bool:
        if self.do_save():
            self.set_modified(False)
            return True

        return False
