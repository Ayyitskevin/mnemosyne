"""Inference-cost metering — the COGS spine that lets us price Phase 2.

These encode *why* metering exists and how it must read: every BILLED cloud call
records a row, dollars are derived from configurable rates (and are honestly
'unknown' until rates are set, never a fake $0), an album's roll-up only reports a
dollar total when EVERY call was priced, and the rows are tenant-scoped so an album
delete takes its cost trail with it. Local Ollama is free and must never record.
"""
from __future__ import annotations

import pytest

from mnemosyne import arrange, auth, config, db, pipeline, usage, vision


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.migrate(c)
    return c


def _album(conn) -> int:
    uid = auth.create_user(conn, "a@example.com", "pw12345")["id"]
    cur = conn.execute(
        "INSERT INTO albums (name, source_dir, owner_id) VALUES ('a', '/x', ?)", (uid,)
    )
    conn.commit()
    return cur.lastrowid


_TOKENS = {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200}


def test_cost_is_none_when_unpriced(monkeypatch):
    monkeypatch.setattr(config, "GROK_PRICE_PROMPT_PER_M", 0.0)
    monkeypatch.setattr(config, "GROK_PRICE_COMPLETION_PER_M", 0.0)
    # No rate configured -> dollars are unknown, not zero.
    assert usage._cost_usd(1000, 200) is None


def test_cost_uses_separate_prompt_and_completion_rates(monkeypatch):
    monkeypatch.setattr(config, "GROK_PRICE_PROMPT_PER_M", 2.0)     # $2 / 1M prompt
    monkeypatch.setattr(config, "GROK_PRICE_COMPLETION_PER_M", 10.0)  # $10 / 1M completion
    # 1000*2/1e6 + 200*10/1e6 = 0.002 + 0.002 = 0.004
    assert usage._cost_usd(1000, 200) == pytest.approx(0.004)


def test_record_then_summary_rolls_up_priced_calls(conn, monkeypatch):
    monkeypatch.setattr(config, "GROK_PRICE_PROMPT_PER_M", 2.0)
    monkeypatch.setattr(config, "GROK_PRICE_COMPLETION_PER_M", 10.0)
    aid = _album(conn)
    usage.record(conn, album_id=aid, photo_id=1, stage="vision", backend="grok",
                 model="grok-4.20", tokens=_TOKENS, latency=0.5)
    usage.record(conn, album_id=aid, photo_id=None, stage="arrange", backend="grok",
                 model="grok-4.20", tokens=_TOKENS, latency=1.2)

    s = usage.album_summary(conn, aid)
    assert s["calls"] == 2
    assert s["total_tokens"] == 2400
    assert s["cost_usd"] == pytest.approx(0.008)  # two priced calls at 0.004 each


def test_summary_cost_is_unknown_if_any_call_unpriced(conn, monkeypatch):
    # First call is priced, second isn't — a partial sum would lie, so report None.
    aid = _album(conn)
    monkeypatch.setattr(config, "GROK_PRICE_PROMPT_PER_M", 2.0)
    monkeypatch.setattr(config, "GROK_PRICE_COMPLETION_PER_M", 10.0)
    usage.record(conn, album_id=aid, photo_id=1, stage="vision", backend="grok",
                 model="m", tokens=_TOKENS, latency=0.5)
    monkeypatch.setattr(config, "GROK_PRICE_PROMPT_PER_M", 0.0)
    monkeypatch.setattr(config, "GROK_PRICE_COMPLETION_PER_M", 0.0)
    usage.record(conn, album_id=aid, photo_id=2, stage="vision", backend="grok",
                 model="m", tokens=_TOKENS, latency=0.5)

    s = usage.album_summary(conn, aid)
    assert s["calls"] == 2 and s["total_tokens"] == 2400
    assert s["cost_usd"] is None


def test_empty_album_summary_is_zeroed(conn):
    aid = _album(conn)
    assert usage.album_summary(conn, aid) == {"calls": 0, "total_tokens": 0, "cost_usd": None}


def test_cost_report_breaks_down_by_stage(conn, monkeypatch):
    # The Gate 3 number: vision is one billed call per photo, arrange is a single
    # call. The report must split tokens/dollars per stage AND match the roll-up.
    monkeypatch.setattr(config, "GROK_PRICE_PROMPT_PER_M", 2.0)
    monkeypatch.setattr(config, "GROK_PRICE_COMPLETION_PER_M", 10.0)
    aid = _album(conn)
    for pid in (1, 2):  # two billed vision photos
        usage.record(conn, album_id=aid, photo_id=pid, stage="vision", backend="grok",
                     model="m", tokens=_TOKENS, latency=0.3)
    usage.record(conn, album_id=aid, photo_id=None, stage="arrange", backend="grok",
                 model="m", tokens=_TOKENS, latency=1.1)

    rep = usage.album_cost_report(conn, aid)
    assert rep["album_id"] == aid
    assert rep["calls"] == 3 and rep["total_tokens"] == 3600
    assert rep["cost_usd"] == pytest.approx(0.012)  # three priced calls at 0.004
    by_stage = {s["stage"]: s for s in rep["stages"]}
    assert by_stage["vision"]["calls"] == 2
    assert by_stage["vision"]["total_tokens"] == 2400
    assert by_stage["vision"]["cost_usd"] == pytest.approx(0.008)
    assert by_stage["arrange"]["calls"] == 1
    assert by_stage["arrange"]["cost_usd"] == pytest.approx(0.004)
    # Per-stage totals reconcile to the headline — the report can't silently drop a call.
    assert sum(s["calls"] for s in rep["stages"]) == rep["calls"]


def test_cost_report_dollars_unknown_when_unpriced(conn, monkeypatch):
    # No rates configured: tokens are still ground truth, but every dollar reads
    # None (honest "unknown"), headline and per-stage alike.
    monkeypatch.setattr(config, "GROK_PRICE_PROMPT_PER_M", 0.0)
    monkeypatch.setattr(config, "GROK_PRICE_COMPLETION_PER_M", 0.0)
    aid = _album(conn)
    usage.record(conn, album_id=aid, photo_id=1, stage="vision", backend="grok",
                 model="m", tokens=_TOKENS, latency=0.3)
    rep = usage.album_cost_report(conn, aid)
    assert rep["total_tokens"] == 1200
    assert rep["cost_usd"] is None
    assert rep["stages"][0]["stage"] == "vision"
    assert rep["stages"][0]["cost_usd"] is None


def test_cost_report_empty_album_has_no_stages(conn):
    aid = _album(conn)
    rep = usage.album_cost_report(conn, aid)
    assert rep["calls"] == 0 and rep["stages"] == []


def test_look_at_album_records_a_vision_row_per_billed_photo(conn, monkeypatch):
    # A grok vision call attaches usage_meta; look_at_album must turn that into one
    # inference_usage row per photo. Stub analyze_one so no network is touched.
    aid = _album(conn)
    for i in range(3):
        conn.execute(
            "INSERT INTO photos (album_id, storage_key, width, height) "
            "VALUES (?, ?, 100, 100)", (aid, f"a{aid}/p{i}.jpg"),
        )
    conn.commit()
    # Bytes don't matter — open_path just needs the key to resolve; create them.
    store = vision.storage.get_storage()
    for i in range(3):
        store.put(f"a{aid}/p{i}.jpg", b"x")

    monkeypatch.setattr(vision, "analyze_one", lambda path, **kw: {
        "scene": "food", "hero_score": 0.5,
        "usage_meta": {"backend": "grok", "model": "m", "tokens": _TOKENS, "latency": 0.3},
    })
    n = vision.look_at_album(conn, aid)
    assert n == 3

    rows = conn.execute(
        "SELECT stage, backend, total_tokens, photo_id FROM inference_usage "
        "WHERE album_id = ? ORDER BY id", (aid,)
    ).fetchall()
    assert len(rows) == 3
    assert {r["stage"] for r in rows} == {"vision"}
    assert all(r["total_tokens"] == 1200 and r["photo_id"] is not None for r in rows)


def test_local_vision_path_records_nothing(conn, monkeypatch):
    # The free local backend returns no usage_meta -> not a single metering row.
    aid = _album(conn)
    conn.execute(
        "INSERT INTO photos (album_id, storage_key, width, height) "
        "VALUES (?, ?, 100, 100)", (aid, f"a{aid}/p.jpg"),
    )
    conn.commit()
    vision.storage.get_storage().put(f"a{aid}/p.jpg", b"x")

    monkeypatch.setattr(
        vision, "analyze_one", lambda path, **kw: {"scene": "food", "hero_score": 0.5}
    )
    vision.look_at_album(conn, aid)
    assert usage.album_summary(conn, aid)["calls"] == 0


def test_delete_album_clears_its_usage_rows(conn, monkeypatch):
    monkeypatch.setattr(arrange, "_ask_model", lambda photos, **kw: (None, None))
    monkeypatch.setattr(
        vision, "analyze_one", lambda path, **kw: {"scene": "food", "hero_score": 0.5}
    )
    aid = _album(conn)
    usage.record(conn, album_id=aid, photo_id=None, stage="arrange", backend="grok",
                 model="m", tokens=_TOKENS, latency=1.0)
    conn.execute("UPDATE albums SET status = 'ready' WHERE id = ?", (aid,))
    conn.commit()

    assert pipeline.delete_album(conn, aid) is True
    left = conn.execute(
        "SELECT COUNT(*) AS c FROM inference_usage WHERE album_id = ?", (aid,)
    ).fetchone()["c"]
    assert left == 0
