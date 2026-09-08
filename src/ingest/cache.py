"""On-disk cache for retrieved source files.

Journals are large.  We cache by content address so a re-run does not
re-download, and we do not keep raw files longer than configured.
"""

from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path

from src.logging_setup import get_logger

log = get_logger(__name__)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


class FileCache:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, key: str, suffix: str = "") -> Path:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
        return self.root / f"{safe}{suffix}"

    def has(self, key: str, suffix: str = "") -> bool:
        return self.path_for(key, suffix).exists()

    def get(self, key: str, suffix: str = "") -> Path | None:
        p = self.path_for(key, suffix)
        return p if p.exists() else None

    def put_file(self, key: str, src: Path, suffix: str = "") -> Path:
        dest = self.path_for(key, suffix)
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)
        return dest

    def prune(self, max_age_days: int = 30, keep_extensions: tuple[str, ...] = ()) -> int:
        """Delete cached files older than ``max_age_days``. Returns count removed."""
        cutoff = time.time() - max_age_days * 86400
        removed = 0
        for p in self.root.iterdir():
            if not p.is_file():
                continue
            if keep_extensions and p.suffix in keep_extensions:
                continue
            if p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        if removed:
            log.info("cache.pruned", removed=removed, root=str(self.root))
        return removed
