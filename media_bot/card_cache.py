"""Short-lived on-disk cache for rendered X/Reddit card images.

Rendering a card is CPU work and re-fetching the post can hit public APIs. A
cache keyed by platform + post id (plus render variant) serves the same PNG to
everyone until the TTL expires.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

LOG = logging.getLogger(__name__)
CARD_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


@dataclass
class CardEntry:
    path: Path
    files: list[str]
    created_at: float


class CardCache:
    def __init__(self, root: Path | None = None, ttl: float = 1800,
                 max_bytes: int = 256 * 1024 * 1024):
        self.root = root
        self.ttl = ttl
        self.max_bytes = max_bytes
        self._enabled = root is not None
        self.hits = 0
        self.misses = 0
        self.stores = 0

    @property
    def enabled(self) -> bool:
        return self._enabled and self.root is not None

    def configure(self, root: Path, ttl: float = 1800,
                  max_bytes: int = 256 * 1024 * 1024) -> None:
        self.root = root
        self.ttl = ttl
        self.max_bytes = max_bytes
        self._enabled = True
        try:
            self.cleanup()
        except OSError:
            pass

    def _key(self, platform: str, key: str, variant: str = "") -> str:
        raw = f"{platform}\n{key}\n{variant}".encode("utf-8", "ignore")
        return hashlib.sha256(raw).hexdigest()

    def _dir(self, key: str) -> Path:
        assert self.root is not None
        return self.root / key[:2] / key

    def cleanup(self) -> None:
        if not self.root or not self.root.exists():
            return
        now = time.time()
        entries: list[tuple[float, Path, int]] = []
        for meta_path in self.root.rglob("meta.json"):
            base = meta_path.parent
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                created = float(meta.get("created_at", 0))
                files = [str(f) for f in meta.get("files", []) if isinstance(f, str)]
            except (OSError, ValueError, TypeError):
                shutil.rmtree(base, ignore_errors=True)
                continue
            if now - created > self.ttl:
                shutil.rmtree(base, ignore_errors=True)
                continue
            size = 0
            for name in files:
                try:
                    size += (base / name).stat().st_size
                except OSError:
                    pass
            entries.append((created, base, size))
        total = sum(size for _, _, size in entries)
        if total <= self.max_bytes:
            return
        for _, base, size in sorted(entries, key=lambda entry: entry[0]):
            shutil.rmtree(base, ignore_errors=True)
            total -= size
            if total <= self.max_bytes:
                break

    def get(self, platform: str, key: str, variant: str = "") -> CardEntry | None:
        if not self.enabled:
            return None
        base = self._dir(self._key(platform, key, variant))
        try:
            meta = json.loads((base / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            self.misses += 1
            return None
        created = float(meta.get("created_at", 0))
        if time.time() - created > self.ttl:
            shutil.rmtree(base, ignore_errors=True)
            self.misses += 1
            return None
        files = [str(name) for name in meta.get("files", []) if isinstance(name, str)]
        resolved = [name for name in files if (base / name).is_file()]
        if not resolved:
            shutil.rmtree(base, ignore_errors=True)
            self.misses += 1
            return None
        self.hits += 1
        return CardEntry(path=base, files=resolved, created_at=created)

    def put(self, platform: str, key: str, source_files: list[Path],
            variant: str = "") -> CardEntry | None:
        if not self.enabled or not source_files:
            return None
        base = self._dir(self._key(platform, key, variant))
        try:
            self.cleanup()
            if base.exists():
                shutil.rmtree(base, ignore_errors=True)
            base.mkdir(parents=True, exist_ok=True)
            names: list[str] = []
            for source in sorted(source_files):
                if not source.is_file() or source.suffix.lower() not in CARD_SUFFIXES:
                    continue
                destination = base / source.name
                shutil.copy2(source, destination)
                names.append(source.name)
            if not names:
                shutil.rmtree(base, ignore_errors=True)
                return None
            created = time.time()
            (base / "meta.json").write_text(
                json.dumps({"created_at": created, "files": names}), encoding="utf-8"
            )
            self.stores += 1
            self.cleanup()
            return CardEntry(path=base, files=names, created_at=created)
        except OSError as exc:
            LOG.warning("Card cache write failed: %s", exc)
            return None

    def copy_to(self, entry: CardEntry, folder: Path) -> list[Path]:
        folder.mkdir(parents=True, exist_ok=True)
        copied: list[Path] = []
        for name in entry.files:
            source = entry.path / name
            if not source.is_file():
                continue
            destination = folder / name
            try:
                shutil.copy2(source, destination)
                copied.append(destination)
            except OSError:
                continue
        return copied

    def stats(self) -> dict:
        lookups = self.hits + self.misses
        out = {
            "enabled": self.enabled,
            "entries": 0,
            "files": 0,
            "bytes": 0,
            "stale": 0,
            "max_bytes": self.max_bytes,
            "ttl": self.ttl,
            "path": str(self.root) if self.root is not None else None,
            "hits": self.hits,
            "misses": self.misses,
            "stores": self.stores,
            "hit_rate": (self.hits / lookups) if lookups else 0.0,
        }
        if not self.root or not self.root.exists():
            return out
        now = time.time()
        for meta_path in self.root.rglob("meta.json"):
            base = meta_path.parent
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                created = float(meta.get("created_at", 0))
                names = [str(name) for name in meta.get("files", []) if isinstance(name, str)]
            except (OSError, ValueError, TypeError):
                out["stale"] += 1
                continue
            resolved = [name for name in names if (base / name).is_file()]
            if not resolved or now - created > self.ttl:
                out["stale"] += 1
                continue
            out["entries"] += 1
            out["files"] += len(resolved)
            for name in resolved:
                try:
                    out["bytes"] += (base / name).stat().st_size
                except OSError:
                    pass
        return out

    def clear(self) -> int:
        if not self.root or not self.root.exists():
            return 0
        removed = sum(1 for _ in self.root.rglob("meta.json"))
        for child in list(self.root.iterdir()):
            try:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
            except OSError as exc:
                LOG.warning("Card cache clear failed for %s: %s", child, exc)
        return removed


CARD_CACHE = CardCache()
