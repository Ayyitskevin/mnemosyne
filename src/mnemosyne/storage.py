"""Store — the storage seam: the one place that knows where photo bytes live.

Every other station refers to a photo only by its `storage_key` and asks THIS
module to turn that key into bytes (or a servable URL). Nothing else opens an
image off the filesystem directly. That single chokepoint is what lets mnemosyne
move off one box later: swapping the local-disk driver for an S3/R2 driver is a
config flip here, not a hunt through ingest/vision/export/web.

Slice 1 (now): the only driver is `LocalFsStorage`, and for it a key is simply the
absolute path ingest already records — so behavior is byte-identical to before and
the seam is pure indirection. Slice 2 (when real bucket creds exist): add an
`R2Storage` driver with opaque relative keys + `put`-at-ingest; callers don't change
because they already speak only `Storage`. Until then keys ARE local paths, but
callers must treat them as opaque and never `Path(key)` them outside this module.
"""
from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from mnemosyne import config


class Storage(Protocol):
    """What every backend must provide. The pipeline depends on this shape, not on
    any one driver — so a new backend is a new class, not edits to five callers."""

    def exists(self, key: str) -> bool:
        """True if bytes are stored under `key`. Used by the web route to 404 a
        missing photo without leaking whether the id ever existed."""
        ...

    def open_path(self, key: str) -> "contextlib.AbstractContextManager[Path]":
        """A real on-disk path to the bytes, valid for the life of the `with` block.
        Local-fs yields the file itself; a remote driver downloads to a temp file
        and deletes it on exit. The worker and PDF export read fully INSIDE the
        block, so a remote temp can be cleaned up the moment they're done."""
        ...

    def signed_url(self, key: str) -> str | None:
        """A URL the browser can fetch the bytes from directly, or None when the
        backend has no such URL (local disk). The web route redirects to it when
        present and otherwise serves the file itself — so remote bytes never get
        proxied through this process."""
        ...

    def local_path(self, key: str) -> Path:
        """The real path for a backend that has one (local disk). Only called when
        `signed_url` returned None, i.e. on the local driver — a remote driver
        never reaches here. Kept separate from `open_path` because the web route's
        FileResponse streams AFTER the handler returns, so it can't sit inside a
        context manager that might delete the file."""
        ...


class LocalFsStorage:
    """Bytes on this machine's filesystem. A key is an absolute path (the value
    ingest writes today), so this driver is a thin, behavior-preserving wrapper —
    the seam without any data migration. The opaque-key contract still holds: only
    THIS class is allowed to know a local key happens to be a path."""

    def exists(self, key: str) -> bool:
        return Path(key).is_file()

    @contextlib.contextmanager
    def open_path(self, key: str) -> Iterator[Path]:
        # The bytes are already on disk; hand back the path and do nothing on exit
        # (there is no temp to clean up for local storage).
        yield Path(key)

    def signed_url(self, key: str) -> str | None:
        return None

    def local_path(self, key: str) -> Path:
        return Path(key)


_BACKENDS = {"local": LocalFsStorage}
_instance: Storage | None = None


def get_storage() -> Storage:
    """The process-wide storage driver, chosen by MNEMOSYNE_STORAGE_BACKEND.
    Memoized because a driver is stateless config, not per-request state. An
    unknown backend fails loud here rather than silently writing nowhere (R4)."""
    global _instance
    if _instance is None:
        backend = (config.STORAGE_BACKEND or "local").strip().lower()
        try:
            _instance = _BACKENDS[backend]()
        except KeyError:
            raise RuntimeError(
                f"unknown MNEMOSYNE_STORAGE_BACKEND={backend!r}; "
                f"known: {sorted(_BACKENDS)}"
            ) from None
    return _instance
