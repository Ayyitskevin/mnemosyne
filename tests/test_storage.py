"""Storage seam — the contract every byte-access caller leans on.

These encode *why* the seam exists: callers (vision, export, /photo, ingest) reach
bytes ONLY through a driver, so the backend can change without touching them. The
local driver roots opaque, album-scoped keys under UPLOAD_DIR today; the dormant R2
driver will satisfy the same contract against a bucket tomorrow. The cases below
pin the parts that protect data and fail loud: relative keys round-trip, a `../`
key is rejected (not clamped), a pre-turn absolute key still resolves (so old
albums survive the rename), and selecting a backend that can't run blows up at
startup rather than writing nowhere.
"""
import sys
import types

import pytest

from mnemosyne import config, storage


@pytest.fixture(autouse=True)
def _local_backend(tmp_path, monkeypatch):
    # Root the local driver at a temp dir and force the local backend, resetting the
    # memoized singleton on both sides so a backend override here can't leak.
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "STORAGE_BACKEND", "local")
    storage._instance = None
    yield
    storage._instance = None


def test_relative_key_round_trips_through_the_root():
    store = storage.get_storage()
    key = store.put("a7/shot.jpg", b"not really a jpeg, just bytes")

    assert key == "a7/shot.jpg"
    assert store.exists("a7/shot.jpg") is True
    assert store.exists("a7/missing.jpg") is False
    # The bytes landed under UPLOAD_DIR, addressed by the relative key.
    assert store.local_path("a7/shot.jpg") == (config.UPLOAD_DIR / "a7/shot.jpg").resolve()
    assert store.local_path("a7/shot.jpg").read_bytes() == b"not really a jpeg, just bytes"
    # Local has no signable URL, so the route must fall back to serving the file.
    assert store.signed_url("a7/shot.jpg") is None
    with store.open_path("a7/shot.jpg") as p:
        assert p.read_bytes() == b"not really a jpeg, just bytes"


def test_delete_prefix_drops_a_whole_album():
    store = storage.get_storage()
    store.put("a3/one.jpg", b"1")
    store.put("a3/two.jpg", b"2")
    store.put("a4/keep.jpg", b"keep")

    store.delete_prefix("a3/")

    assert store.exists("a3/one.jpg") is False
    assert store.exists("a3/two.jpg") is False
    # A sibling album's bytes are untouched — the prefix is album-scoped.
    assert store.exists("a4/keep.jpg") is True


def test_traversal_key_is_rejected_loud(tmp_path):
    # A `../` key that would escape the upload root must raise, never silently write
    # outside it. This is the guard that keeps a doctored key from reaching the disk.
    store = storage.get_storage()
    with pytest.raises(storage.StorageError, match="escapes storage root"):
        store.put("../escape.jpg", b"nope")
    with pytest.raises(storage.StorageError):
        store.exists("a1/../../etc/passwd")


def test_legacy_absolute_key_still_resolves(tmp_path):
    # Albums ingested before the key turn stored an absolute disk path. The local
    # driver honors it as-is so those albums stay viewable after the rename (R22).
    legacy = tmp_path / "old_gallery" / "pre_turn.jpg"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"a dogfood photo from before the turn")
    store = storage.get_storage()

    key = str(legacy)
    assert store.exists(key) is True
    assert store.local_path(key) == legacy
    assert store.signed_url(key) is None
    with store.open_path(key) as p:
        assert p.read_bytes() == legacy.read_bytes()


def test_get_storage_is_memoized():
    assert storage.get_storage() is storage.get_storage()


def test_unknown_backend_fails_loud(monkeypatch):
    # A typo'd backend must blow up at startup, not silently write nowhere (R4).
    monkeypatch.setattr(config, "STORAGE_BACKEND", "dropbox")
    with pytest.raises(RuntimeError, match="unknown MNEMOSYNE_STORAGE_BACKEND"):
        storage.get_storage()


def test_r2_backend_without_sdk_fails_loud(monkeypatch):
    # The R2 driver is dormant: selecting it without boto3 installed must fail loud
    # with an actionable message, never silently behave like local disk. (boto3 is
    # intentionally absent from the test/dev env until a real bucket is wired up.)
    try:
        import boto3  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("boto3 is installed; this pins the no-SDK fail-loud path")
    monkeypatch.setattr(config, "STORAGE_BACKEND", "r2")
    with pytest.raises(RuntimeError, match="pip install boto3"):
        storage.get_storage()


# --- R2 driver, exercised against a fake S3 client ---------------------------
#
# The R2 backend ships dormant (no live bucket, boto3 absent from the dev env), but
# "dormant" must not mean "unverified" (R9/R21): a backend that silently misbehaves
# the day creds land is worse than none. So we inject a fake boto3/botocore over an
# in-memory bucket and drive the same contract the local driver passes. The SDK is
# faked via sys.modules, NOT installed — so the no-SDK fail-loud test above stays
# honest (boto3 really is absent everywhere else).


class _ClientError(Exception):
    """Stand-in for botocore.exceptions.ClientError: an error carrying the AWS
    error code in .response, which exists() inspects to tell 'missing' from 'broke'."""

    def __init__(self, code: str):
        self.response = {"Error": {"Code": code}}
        super().__init__(code)


class _FakeS3:
    """An in-memory S3 client over one dict 'bucket' — only the calls R2Storage
    actually makes are implemented, each mirroring boto3's real signature."""

    def __init__(self, bucket: dict):
        self._bucket = bucket

    def head_object(self, Bucket, Key):
        if Key not in self._bucket:
            raise _ClientError("404")
        return {"ContentLength": len(self._bucket[Key])}

    def put_object(self, Bucket, Key, Body):
        self._bucket[Key] = Body
        return {}

    def download_fileobj(self, Bucket, Key, fileobj):
        if Key not in self._bucket:
            raise _ClientError("NoSuchKey")
        fileobj.write(self._bucket[Key])

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return f"https://signed.r2.test/{Params['Key']}?exp={ExpiresIn}"

    def get_paginator(self, name):
        bucket = self._bucket

        class _Paginator:
            def paginate(self, Bucket, Prefix):
                hits = [{"Key": k} for k in sorted(bucket) if k.startswith(Prefix)]
                yield {"Contents": hits}

        return _Paginator()

    def delete_objects(self, Bucket, Delete):
        for o in Delete["Objects"]:
            self._bucket.pop(o["Key"], None)
        return {}


@pytest.fixture
def r2(monkeypatch):
    """Force the R2 backend with a fake SDK injected; yields the backing dict (the
    'bucket') so a test can inspect what landed. Resets the memoized singleton so the
    autouse local override can't leak in, and so re-pointing config inside a test can
    rebuild the driver."""
    bucket: dict = {}

    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = lambda *a, **k: _FakeS3(bucket)
    fake_botocore = types.ModuleType("botocore")
    fake_exc = types.ModuleType("botocore.exceptions")
    fake_exc.ClientError = _ClientError
    fake_botocore.exceptions = fake_exc
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setitem(sys.modules, "botocore", fake_botocore)
    monkeypatch.setitem(sys.modules, "botocore.exceptions", fake_exc)

    monkeypatch.setattr(config, "STORAGE_BACKEND", "r2")
    monkeypatch.setattr(config, "R2_ENDPOINT", "https://acct.r2.cloudflarestorage.com")
    monkeypatch.setattr(config, "R2_BUCKET", "mnemo-test")
    monkeypatch.setattr(config, "R2_ACCESS_KEY_ID", "ak")
    monkeypatch.setattr(config, "R2_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setattr(config, "R2_SIGNED_URL_TTL", 900)
    monkeypatch.setattr(config, "R2_PUBLIC_BASE_URL", None)
    storage._instance = None
    yield bucket
    storage._instance = None


def test_r2_put_exists_and_open_round_trip(r2):
    store = storage.get_storage()
    assert isinstance(store, storage.R2Storage)

    key = store.put("a12/sunset.jpg", b"R2 bytes")
    assert key == "a12/sunset.jpg"
    assert r2["a12/sunset.jpg"] == b"R2 bytes"  # the bytes entered the bucket via put
    assert store.exists("a12/sunset.jpg") is True
    assert store.exists("a12/missing.jpg") is False

    with store.open_path("a12/sunset.jpg") as p:
        assert p.read_bytes() == b"R2 bytes"
    # The remote driver downloads to a temp file and cleans it up on block exit.
    assert not p.exists()


def test_r2_signed_url_presigns_when_no_public_base(r2):
    store = storage.get_storage()
    store.put("a1/x.jpg", b"x")
    url = store.signed_url("a1/x.jpg")
    # With no public CDN base, the browser gets a time-limited presigned GET.
    assert url.startswith("https://signed.r2.test/a1/x.jpg")
    assert "exp=900" in url


def test_r2_signed_url_prefers_public_base(r2, monkeypatch):
    # A public bucket base means we hand out a plain CDN URL, no presign round-trip.
    monkeypatch.setattr(config, "R2_PUBLIC_BASE_URL", "https://cdn.example.com/")
    storage._instance = None  # rebuild so __init__ re-reads the base
    store = storage.get_storage()
    assert store.signed_url("a1/x.jpg") == "https://cdn.example.com/a1/x.jpg"


def test_r2_delete_prefix_scopes_to_album(r2):
    store = storage.get_storage()
    store.put("a3/one.jpg", b"1")
    store.put("a3/two.jpg", b"2")
    store.put("a4/keep.jpg", b"k")

    store.delete_prefix("a3/")

    assert store.exists("a3/one.jpg") is False
    assert store.exists("a3/two.jpg") is False
    assert store.exists("a4/keep.jpg") is True  # sibling album untouched


def test_r2_local_path_refuses(r2):
    # R2 always serves via signed_url, so the web route never asks it for a disk
    # path; if it somehow does, that's a bug and must fail loud, not return junk.
    store = storage.get_storage()
    with pytest.raises(storage.StorageError, match="no local path"):
        store.local_path("a1/x.jpg")


def test_r2_exists_reraises_a_non_404_error(r2, monkeypatch):
    # A real backend fault (auth, outage) must propagate — swallowing it as "object
    # doesn't exist" would mask an outage as a missing photo (R21).
    store = storage.get_storage()

    def boom(self, Bucket, Key):
        raise _ClientError("InternalError")

    monkeypatch.setattr(_FakeS3, "head_object", boom)
    with pytest.raises(_ClientError) as ei:
        store.exists("a1/x.jpg")
    assert ei.value.response["Error"]["Code"] == "InternalError"


def test_r2_missing_config_fails_loud(r2, monkeypatch):
    # SDK present but a cred missing must still blow up at construction, naming the
    # absent var, never quietly half-configured.
    monkeypatch.setattr(config, "R2_BUCKET", None)
    storage._instance = None
    with pytest.raises(RuntimeError, match="R2_BUCKET"):
        storage.get_storage()
