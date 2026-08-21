from pathlib import Path

import pytest

from app.storage import LocalAssetStore


def test_atomic_write_and_hash(tmp_path: Path) -> None:
    store = LocalAssetStore(tmp_path)
    asset = store.write_bytes("projects/a/source.txt", b"hello")
    assert asset.sha256 == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert store.read_bytes(asset.relative_path) == b"hello"
    assert not list(tmp_path.rglob("*.part"))


def test_path_traversal_is_rejected(tmp_path: Path) -> None:
    store = LocalAssetStore(tmp_path)
    with pytest.raises(ValueError):
        store.write_bytes("../outside.txt", b"no")
