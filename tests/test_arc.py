"""Tests for the narrative-arc classifier.

These pin the behavior that matters for sequencing: representative scene labels from
each theme's vision vocabulary land in arc order (arrival < hero < closing), an
end-stage label is NOT pulled forward by a word it happens to share with a mid-arc
bucket, and an unlabelled/unmatched shot falls to the middle so the sequence stays
chronological where the arc can't classify.
"""
from __future__ import annotations

from mnemosyne import arc


def _idx(theme: str, scene: str) -> float:
    return arc.bucket_index(theme, scene)


def test_food_labels_sort_in_arc_order():
    seq = [
        "wide interior establishing shot",
        "cocktail/drink detail",
        "overhead hero plated dish",
        "chef plating action",
        "closing ambiance shot",
    ]
    indices = [_idx("food", s) for s in seq]
    assert indices == sorted(indices)
    # The closing shot is genuinely last, not pulled forward by "ambiance".
    assert _idx("food", "closing ambiance shot") == max(indices)


def test_wedding_labels_sort_in_arc_order():
    seq = [
        "getting ready detail",
        "wide ceremony establishing shot",
        "couple portrait",
        "family group",
        "reception candids",
        "first dance",
        "send-off moment",
    ]
    indices = [_idx("wedding", s) for s in seq]
    assert indices == sorted(indices)


def test_general_closing_not_pulled_forward_by_mood():
    # "closing mood shot" must map to closing even though atmosphere is a mid bucket.
    assert _idx("general", "closing mood shot") == max(
        _idx("general", s)
        for s in ("environment establishing shot", "subject portrait",
                  "closing mood shot")
    )


def test_event_labels_sort_in_arc_order():
    seq = [
        "venue wide shot",
        "speaker or stage moment",
        "audience reaction",
        "branding detail",
        "group photo",
        "closing shot",
    ]
    indices = [_idx("event", s) for s in seq]
    assert indices == sorted(indices)


def test_unmatched_and_empty_scene_go_to_the_middle():
    middle = len(arc.buckets("food")) / 2.0
    assert _idx("food", "something the arc has no word for") == middle
    assert _idx("food", "") == middle
    assert _idx("food", None) == middle


def test_unknown_theme_falls_back_to_a_known_arc():
    # normalize_theme maps an unknown theme to a real one, so this never KeyErrors.
    assert isinstance(_idx("nonsense-theme", "hero plated dish"), float)
