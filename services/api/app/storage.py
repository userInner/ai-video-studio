from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StoredAsset:
    relative_path: str
    sha256: str
    size: int


class LocalAssetStore:
    """Atomic, path-safe local asset persistence."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("asset path escapes storage root")
        return candidate

    def write_bytes(self, relative_path: str, content: bytes) -> StoredAsset:
        target = self._resolve(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
        temporary.write_bytes(content)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        return StoredAsset(relative_path, hashlib.sha256(content).hexdigest(), len(content))

    def write_json(self, relative_path: str, payload: dict | list) -> StoredAsset:
        content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        return self.write_bytes(relative_path, content)

    def read_bytes(self, relative_path: str) -> bytes:
        return self._resolve(relative_path).read_bytes()

    def path_for_read(self, relative_path: str) -> Path:
        path = self._resolve(relative_path)
        if not path.is_file():
            raise FileNotFoundError(relative_path)
        return path
