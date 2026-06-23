"""Tests for the PDF export geometry. The aesthetic call belongs to the eye, but
the arithmetic that turns the layout engine's fr-tracks into page rectangles must
be exact: every photo's rect has to stay inside the spread, areas must not overlap,
and a hero that spans cells (wide across the top, or tall down one page) must get a
rect that actually covers that span — otherwise the print silently crops or doubles
a shot. These encode that geometry, not just that a PDF gets written."""
from __future__ import annotations

from mnemosyne import export


def test_parse_tracks_strips_fr_units():
    assert export._parse_tracks("1.3fr 1fr") == [1.3, 1.0]


def test_parse_areas_builds_the_name_matrix():
    assert export._parse_areas('"a a" "b c"') == [["a", "a"], ["b", "c"]]


def test_single_area_fills_the_whole_spread():
    rects = export._area_rects(
        {"cols": "1fr", "rows": "1fr", "areas": '"a"'}, 0, 100, 200, 100
    )
    x, y, w, h = rects["a"]
    assert (x, y, w, h) == (0, 0, 200, 100)


def test_wide_hero_spans_both_columns_on_top():
    # "a a" / "b c": hero a is the full-width top band; b and c split the bottom.
    rects = export._area_rects(
        {"cols": "1fr 1fr", "rows": "1fr 1fr", "areas": '"a a" "b c"'},
        0, 100, 200, 100,
    )
    ax, ay, aw, ah = rects["a"]
    bx, by, bw, bh = rects["b"]
    cx, cy, cw, ch = rects["c"]
    # Hero is wider than either supporting shot and sits above them.
    assert aw > bw and aw > cw
    assert ay > by and ay > cy
    # b is left of c, neither overlaps the other.
    assert bx < cx
    assert bx + bw <= cx


def test_tall_hero_spans_both_rows_down_one_page():
    # "a b" / "a c": hero a runs the full height of the left column.
    rects = export._area_rects(
        {"cols": "1.3fr 1fr", "rows": "1fr 1fr", "areas": '"a b" "a c"'},
        0, 100, 200, 100,
    )
    ax, ay, aw, ah = rects["a"]
    _, _, _, bh = rects["b"]
    # Hero is taller than a single supporting shot (it spans both rows).
    assert ah > bh
    # And it hugs the left edge, top-aligned to the spread.
    assert ax == 0
    assert ay + ah == 100


def test_no_area_escapes_the_spread_box():
    rects = export._area_rects(
        {"cols": "1fr 1fr 1fr", "rows": "1.5fr 1fr", "areas": '"a a a" "b c d"'},
        10, 110, 200, 100,
    )
    eps = 1e-6
    for x, y, w, h in rects.values():
        assert x >= 10 - eps and x + w <= 210 + eps
        assert y >= 10 - eps and y + h <= 110 + eps


def test_cover_crop_matches_target_aspect():
    from PIL import Image

    wide = Image.new("RGB", (1200, 800))
    out = export._cover(wide, 100, 100)  # ask for a square
    ow, oh = out.size
    assert abs(ow / oh - 1.0) < 0.01  # cropped to ~1:1, no distortion
