"""Test-wide isolation for the storage seam.

Ingest now `put`s photo bytes through the storage driver, and the local driver
roots those writes at config.UPLOAD_DIR. Without this fixture any test that runs
ingest (worker, delete, CLI build) would write real files into the repo's default
`./uploads` — and the memoized driver singleton would leak a backend choice from
one test into the next. This autouse fixture points UPLOAD_DIR at a per-test temp
dir and resets the singleton on both sides, so storage state can't escape a test.
Tests that set their own UPLOAD_DIR still win — their monkeypatch runs after this.
"""
import pytest

from mnemosyne import config, storage


@pytest.fixture(autouse=True)
def _isolate_storage(tmp_path, monkeypatch):
    # .env may point at MinIO/R2 for prod dogfood — tests always use local disk.
    monkeypatch.setattr(config, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    storage._instance = None
    yield
    storage._instance = None
