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
