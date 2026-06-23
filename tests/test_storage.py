"""Storage seam — the contract every byte-access caller leans on.

These encode *why* the seam exists: callers (vision, export, /photo) must reach
bytes ONLY through a driver, so the backend can change without touching them. The
local driver is a thin path wrapper today, but the contract it satisfies is what an
object-store driver will satisfy tomorrow.
"""
import pytest

from mnemosyne import config, storage


@pytest.fixture(autouse=True)
def _reset_storage_singleton():
    # get_storage() memoizes; reset around each test so a backend override here
    # can't leak into the next test (or out of the real app).
    storage._instance = None
    yield
    storage._instance = None


def test_local_backend_round_trips_a_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE_BACKEND", "local")
    f = tmp_path / "shot.jpg"
    f.write_bytes(b"not really a jpeg, just bytes")
    store = storage.get_storage()

    assert store.exists(str(f)) is True
    assert store.exists(str(tmp_path / "missing.jpg")) is False
    # Local has no signable URL, so the route must fall back to serving the file.
    assert store.signed_url(str(f)) is None
    assert store.local_path(str(f)).read_bytes() == f.read_bytes()
    with store.open_path(str(f)) as p:
        assert p.read_bytes() == f.read_bytes()


def test_get_storage_is_memoized(monkeypatch):
    monkeypatch.setattr(config, "STORAGE_BACKEND", "local")
    assert storage.get_storage() is storage.get_storage()


def test_unknown_backend_fails_loud(monkeypatch):
    # A typo'd backend must blow up at startup, not silently write nowhere (R4).
    monkeypatch.setattr(config, "STORAGE_BACKEND", "dropbox")
    with pytest.raises(RuntimeError, match="unknown MNEMOSYNE_STORAGE_BACKEND"):
        storage.get_storage()
