"""Short-lived on-disk cache for downloaded media results.

Used to avoid re-downloading the same URL when requested again soon (including
by different users). Separate from highlight cache which has its own quota logic.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

LOG = logging.getLogger(__name__)
MEDIA_SUFFIXES = {".mp4", ".mov", ".webm", ".m4v", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp3"}


@dataclass
class CacheEntry:
    path: Path
    files: list[str]
    provider: str
    created_at: float


class MediaCache:
    def __init__(self, root: Path | None = None, ttl: float = 6 * 3600, max_bytes: int = 1024 * 1024 * 1024):
        self.root = root
        self.ttl = ttl
        self.max_bytes = max_bytes
        self._inflight: dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()
        self._enabled = root is not None
        self.hits = 0
        self.misses = 0
        self.stores = 0

    @property
    def enabled(self) -> bool:
        return self._enabled and self.root is not None

    def configure(self, root: Path, ttl: float = 6 * 3600, max_bytes: int = 1024 * 1024 * 1024):
        self.root = root
        self.ttl = ttl
        self.max_bytes = max_bytes
        self._enabled = True
        try:
            self.cleanup()
        except OSError:
            pass

    def _key(self, platform: str, url: str, cookie: bool, variant: str = "") -> str:
        raw = f"{platform}\n{url}\n{int(cookie)}\n{variant}".encode("utf-8", "ignore")
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
        total = sum(s for _, _, s in entries)
        if total <= self.max_bytes:
            return
        for _, base, size in sorted(entries, key=lambda e: e[0]):
            shutil.rmtree(base, ignore_errors=True)
            total -= size
            if total <= self.max_bytes:
                break

    def get(self, platform: str, url: str, cookie: bool, variant: str = "") -> CacheEntry | None:
        if not self.enabled:
            return None
        key = self._key(platform, url, cookie, variant)
        base = self._dir(key)
        meta_path = base / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            self.misses += 1
            return None
        created = float(meta.get("created_at", 0))
        if time.time() - created > self.ttl:
            shutil.rmtree(base, ignore_errors=True)
            self.misses += 1
            return None
        provider = str(meta.get("provider") or "cache")
        files = [str(f) for f in meta.get("files", []) if isinstance(f, str)]
        resolved = [name for name in files if (base / name).is_file()]
        if not resolved:
            shutil.rmtree(base, ignore_errors=True)
            self.misses += 1
            return None
        self.hits += 1
        return CacheEntry(path=base, files=resolved, provider=provider, created_at=created)

    def put(self, platform: str, url: str, cookie: bool, provider: str, source_files: list[Path], variant: str = "") -> CacheEntry | None:
        if not self.enabled or not source_files:
            return None
        key = self._key(platform, url, cookie, variant)
        base = self._dir(key)
        try:
            self.cleanup()
            if base.exists():
                shutil.rmtree(base, ignore_errors=True)
            base.mkdir(parents=True, exist_ok=True)
            names: list[str] = []
            for src in sorted(source_files):
                if not src.is_file() or src.suffix.lower() not in MEDIA_SUFFIXES:
                    continue
                dest = base / src.name
                shutil.copy2(src, dest)
                names.append(src.name)
            if not names:
                shutil.rmtree(base, ignore_errors=True)
                return None
            meta = {"created_at": time.time(), "files": names, "provider": provider}
            (base / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
            self.stores += 1
            self.cleanup()
            return CacheEntry(path=base, files=names, provider=provider, created_at=meta["created_at"])
        except OSError as exc:
            LOG.warning("Media cache write failed: %s", exc)
            return None

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
                names = [str(f) for f in meta.get("files", []) if isinstance(f, str)]
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
        """Delete every cached entry and return how many were removed."""
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
                LOG.warning("Media cache clear failed for %s: %s", child, exc)
        return removed

    def copy_to(self, entry: CacheEntry, target_folder: Path) -> list[Path]:
        target_folder.mkdir(parents=True, exist_ok=True)
        copied: list[Path] = []
        for name in entry.files:
            src = entry.path / name
            if not src.is_file():
                continue
            dest = target_folder / name
            # avoid overwriting different content with same name across providers
            if dest.exists():
                stem, suf = dest.stem, dest.suffix
                i = 2
                while dest.exists():
                    dest = target_folder / f"{stem}_{i}{suf}"
                    i += 1
            try:
                shutil.copy2(src, dest)
                copied.append(dest)
            except OSError:
                continue
        return copied

    async def singleflight_start(self, key: str):
        async with self._lock:
            if key in self._inflight:
                return False, self._inflight[key]
            fut = asyncio.get_running_loop().create_future()
            self._inflight[key] = fut
            return True, fut

    async def singleflight_finish(self, key: str, result=None, exc: Exception | None = None):
        async with self._lock:
            fut = self._inflight.pop(key, None)
        if fut and not fut.done():
            if exc is not None:
                fut.set_exception(exc)
            else:
                fut.set_result(result)

    def key_for(self, platform: str, url: str, cookie: bool, variant: str = "") -> str:
        return self._key(platform, url, cookie, variant)


MEDIA_CACHE = MediaCache()
