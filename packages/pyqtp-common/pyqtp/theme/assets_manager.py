from __future__ import annotations

from pathlib import Path


class AssetsManager:
    _paths: list[Path]

    def __init__(self):
        import pyqtp.assets  # noqa: PLC0415

        module = pyqtp.assets
        self._paths = [Path(nsp) for nsp in module.__path__]  # type: ignore[reportAttributeAccessIssue]

    def _path_get(
        self, relative_path: str, *, check_file: bool = True
    ) -> Path | None:
        for p in self._paths:
            full_path = p / relative_path
            if full_path.exists() and (not check_file or full_path.is_file()):
                return full_path

        return None

    def doc_path_get(self, relative_path: str) -> Path | None:
        return self._path_get(f'docs/{relative_path}')

    def help_path_get(self, relative_path: str) -> Path | None:
        return self._path_get(f'help/{relative_path}')

    def image_path_get(self, relative_path: str) -> Path | None:
        return self._path_get(f'images/{relative_path}')

    def asset_path(self, path: str) -> Path | None:
        return self._path_get(path)


if __name__ == '__main__':
    assets_manager = AssetsManager()
    assert assets_manager.image_path_get('io/open.svg') is not None
