"""Store — the storage seam: the one place that knows where photo bytes live.

Every other station refers to a photo only by its `storage_key` and asks THIS
module to turn that key into bytes (or a servable URL). Nothing else opens an
image off the filesystem directly. That single chokepoint is what lets mnemosyne
move off one box later: swapping the local-disk driver for an S3/R2 driver is a
config flip here, not a hunt through ingest/vision/export/web.

A key is OPAQUE to callers — album-scoped and relative, e.g. `a12/sunset.jpg`.
Only a driver knows how to turn that into bytes. `LocalFsStorage` roots keys
under `config.UPLOAD_DIR`; `R2Storage` (dormant until real bucket creds exist)
would map the same key to an object in a bucket. Because callers speak only the
`Storage` shape, neither rename needs to touch ingest/vision/export/web.

Legacy note: albums ingested before the key turn stored an ABSOLUTE disk path in
this column. `LocalFsStorage` honors an absolute key as-is (passthrough) so those
old albums stay viewable after the rename — new albums get relative keys (R22).
"""
from __future__ import annotations

import contextlib
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from mnemosyne import config


class StorageError(Exception):
    """A storage operation that must fail loud: a key that escapes the root, a
    backend asked to do something it can't. Never swallowed into a silent no-op."""


class Storage(Protocol):
    """What every backend must provide. The pipeline depends on this shape, not on
    any one driver — so a new backend is a new class, not edits to five callers."""

    def exists(self, key: str) -> bool:
        """True if bytes are stored under `key`. Used by the web route to 404 a
        missing photo without leaking whether the id ever existed."""
        ...

    def put(self, key: str, data: bytes) -> str:
        """Write `data` under `key`, returning the key. Ingest calls this once per
        photo so the bytes enter storage THROUGH the driver — that is what makes
        an object-store backend a config flip rather than a rewrite of ingest."""
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

    def delete_prefix(self, prefix: str) -> None:
        """Remove every object whose key starts with `prefix`. Album deletion calls
        this with `a<id>/` to drop a whole gallery's bytes in one shot, the storage
        analogue of the cascade delete in the database."""
        ...


class LocalFsStorage:
    """Bytes on this machine's filesystem, rooted under `config.UPLOAD_DIR`. A key
    is a relative path within that root (`a12/sunset.jpg`); the root is read LIVE on
    every call so a test (or a relocated install) can repoint UPLOAD_DIR without
    rebuilding the singleton. The opaque-key contract still holds: only THIS class
    is allowed to know a local key happens to resolve to a path."""

    def _root(self) -> Path:
        return config.UPLOAD_DIR.resolve()

    def _resolve(self, key: str) -> Path:
        """Map an opaque key to a real path under the upload root. An ABSOLUTE key
        is a pre-turn legacy value and is honored as-is (those files live wherever
        ingest first recorded them). A relative key is joined under the root, and a
        `../` that would escape the root is rejected loud, never silently clamped."""
        p = Path(key)
        if p.is_absolute():
            return p
        root = self._root()
        full = (root / key).resolve()
        if not full.is_relative_to(root):
            raise StorageError(f"key escapes storage root: {key!r}")
        return full

    def exists(self, key: str) -> bool:
        return self._resolve(key).is_file()

    def put(self, key: str, data: bytes) -> str:
        full = self._resolve(key)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(data)
        return key

    @contextlib.contextmanager
    def open_path(self, key: str) -> Iterator[Path]:
        # The bytes are already on disk; hand back the path and do nothing on exit
        # (there is no temp to clean up for local storage).
        yield self._resolve(key)

    def signed_url(self, key: str) -> str | None:
        return None

    def local_path(self, key: str) -> Path:
        return self._resolve(key)

    def delete_prefix(self, prefix: str) -> None:
        # A relative prefix names a subtree of the upload root (our album folders);
        # an absolute prefix is a legacy key with no folder under our root, so there
        # is nothing prefix-scoped to remove here (the source dir is cleaned upstream).
        if Path(prefix).is_absolute():
            return
        root = self._root()
        target = (root / prefix).resolve()
        if target == root or not target.is_relative_to(root):
            return
        shutil.rmtree(target, ignore_errors=True)


class R2Storage:
    """Cloudflare R2 (S3-compatible) object storage — DORMANT / UNVERIFIED.

    The whole reason the seam exists, but not yet wired to a live bucket: this code
    has never run against R2 because there are no creds yet. It is here so the
    object-store shape is real and reviewable, and so selecting MNEMOSYNE_STORAGE_
    BACKEND=r2 fails loud (missing boto3 / missing config) instead of silently
    behaving like local disk. boto3 is imported lazily so the local path never pays
    for an SDK it doesn't use. Do NOT treat this as tested until it has round-tripped
    a real bucket with credentials in a .env (off the repo, never committed).
    """

    def __init__(self) -> None:
        try:
            import boto3  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "MNEMOSYNE_STORAGE_BACKEND=r2 needs the AWS SDK: pip install boto3"
            ) from e
        missing = [
            name
            for name, val in (
                ("MNEMOSYNE_R2_ENDPOINT", config.R2_ENDPOINT),
                ("MNEMOSYNE_R2_BUCKET", config.R2_BUCKET),
                ("MNEMOSYNE_R2_ACCESS_KEY_ID", config.R2_ACCESS_KEY_ID),
                ("MNEMOSYNE_R2_SECRET_ACCESS_KEY", config.R2_SECRET_ACCESS_KEY),
            )
            if not val
        ]
        if missing:
            raise RuntimeError(
                f"MNEMOSYNE_STORAGE_BACKEND=r2 is missing config: {', '.join(missing)}"
            )
        self._bucket = config.R2_BUCKET
        self._public_base = (config.R2_PUBLIC_BASE_URL or "").rstrip("/")
        self._ttl = config.R2_SIGNED_URL_TTL

    def _client(self):
        import boto3

        return boto3.client(
            "s3",
            endpoint_url=config.R2_ENDPOINT,
            aws_access_key_id=config.R2_ACCESS_KEY_ID,
            aws_secret_access_key=config.R2_SECRET_ACCESS_KEY,
            region_name="auto",
        )

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client().head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def put(self, key: str, data: bytes) -> str:
        self._client().put_object(Bucket=self._bucket, Key=key, Body=data)
        return key

    @contextlib.contextmanager
    def open_path(self, key: str) -> Iterator[Path]:
        import tempfile

        tmp = tempfile.NamedTemporaryFile(suffix=Path(key).suffix, delete=False)
        try:
            self._client().download_fileobj(self._bucket, key, tmp)
            tmp.close()
            yield Path(tmp.name)
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    def signed_url(self, key: str) -> str | None:
        if self._public_base:
            return f"{self._public_base}/{key}"
        return self._client().generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=self._ttl,
        )

    def local_path(self, key: str) -> Path:
        # Never reached: signed_url always returns a URL for R2, so the web route
        # redirects and never asks a remote backend for an on-disk path.
        raise StorageError("R2Storage has no local path; serve via signed_url")

    def delete_prefix(self, prefix: str) -> None:
        client = self._client()
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if objs:
                client.delete_objects(Bucket=self._bucket, Delete={"Objects": objs})


_BACKENDS = {"local": LocalFsStorage, "r2": R2Storage}
_instance: Storage | None = None


def get_storage() -> Storage:
    """The process-wide storage driver, chosen by MNEMOSYNE_STORAGE_BACKEND.
    Memoized because a driver is stateless config, not per-request state. An
    unknown backend fails loud here rather than silently writing nowhere (R4)."""
    global _instance
    if _instance is None:
        backend = (config.STORAGE_BACKEND or "local").strip().lower()
        try:
            factory = _BACKENDS[backend]
        except KeyError:
            raise RuntimeError(
                f"unknown MNEMOSYNE_STORAGE_BACKEND={backend!r}; "
                f"known: {sorted(_BACKENDS)}"
            ) from None
        _instance = factory()
    return _instance
