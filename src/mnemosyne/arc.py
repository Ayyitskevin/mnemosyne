"""Narrative arc — map a photo's scene label to its place in the shoot's story.

The arrange prompts in themes.py describe each shoot type's story as prose (a food
shoot runs arrival -> ambiance -> details -> hero dishes -> action -> dessert ->
closing). This module turns that prose into an ordered, keyword-matched bucket list
so the deterministic curator can SEQUENCE photos along the arc instead of leaving
them in capture order — a strong, reproducible improvement that needs no model.

`bucket_index(theme, scene)` returns a sortable position: the index of the first
arc bucket whose keywords appear in the (free-text) scene label, or a middle
fallback for an unlabelled/unmatched shot so it lands in the body of the album and
ties break by capture order. Matching is forgiving substring matching because the
vision scene labels are short free text ("wide ceremony establishing shot").
"""
from __future__ import annotations

from mnemosyne.themes import normalize_theme

# Each theme: an ordered list of (bucket name, [keywords]) tracing its story arc.
# Order IS the narrative order; keywords are lowercase substrings matched against the
# scene label and ties go to the EARLIEST bucket. The lists are tuned against the
# vision vocabulary in themes.py so an end-stage label ("closing ambiance shot") is
# not pulled forward by a word it shares with a mid-arc bucket — i.e. a word that
# appears in a "closing" label (ambiance, mood) is deliberately NOT a mid-arc keyword.
ARCS: dict[str, list[tuple[str, list[str]]]] = {
    "food": [
        ("arrival", ["exterior", "arrival", "facade", "storefront", "entrance", "sign"]),
        ("ambiance", ["interior", "establishing", "wide"]),
        ("details", ["detail", "drink", "cocktail", "menu", "ingredient", "macro"]),
        ("hero", ["hero", "plated", "dish", "entree", "plate", "overhead"]),
        ("action", ["action", "plating", "chef", "cooking", "prep", "kitchen", "pour", "service"]),
        ("dessert", ["dessert", "sweet", "cake", "pastry"]),
        ("closing", ["closing", "night", "exit", "final", "farewell"]),
    ],
    "wedding": [
        ("getting ready", ["getting ready", "prep", "robe", "makeup", "hair", "dressing"]),
        ("details", ["ring", "bouquet", "flowers", "dress", "shoes", "invitation", "detail"]),
        ("ceremony", ["ceremony", "vows", "aisle", "altar", "processional"]),
        ("portraits", ["couple portrait", "portrait", "newlywed", "bride and groom"]),
        ("family", ["family", "wedding party", "bridesmaid", "groomsmen"]),
        ("reception", ["reception", "venue", "cocktail hour"]),
        ("toasts", ["toast", "speech", "cake cutting"]),
        ("dancing", ["dance", "dancing", "celebration", "party"]),
        ("closing", ["send-off", "send off", "sendoff", "exit", "closing", "sparkler"]),
    ],
    "general": [
        ("establishing", ["establishing", "context", "wide", "exterior"]),
        ("subjects", ["subject", "portrait", "relationship", "people", "candid interaction"]),
        ("details", ["detail", "close-up", "closeup", "macro"]),
        ("atmosphere", ["atmosphere", "environment", "ambiance", "ambience"]),
        ("hero", ["hero", "strongest", "highlight"]),
        ("closing", ["closing", "final", "exit"]),
    ],
    "event": [
        ("arrival", ["venue", "arrival", "exterior", "registration", "entrance"]),
        ("main", ["keynote", "stage", "speaker", "presentation", "session", "panel"]),
        ("networking", ["networking", "audience", "crowd", "mingling", "reaction"]),
        ("details", ["branding", "detail", "signage", "booth", "logo"]),
        ("group", ["group", "team", "headshot"]),
        ("highlights", ["highlight", "energy", "celebration", "award"]),
        ("closing", ["closing", "final", "exit", "after"]),
    ],
}


def buckets(theme: str | None) -> list[tuple[str, list[str]]]:
    """The arc bucket list for a theme (falls back to a known theme via themes)."""
    return ARCS[normalize_theme(theme)]


def bucket_index(theme: str | None, scene: str | None) -> float:
    """Sortable arc position for one photo. The index of the first bucket whose
    keywords occur in the scene label; for an unlabelled or unmatched shot, a middle
    fallback (len/2) so it sits in the body of the album and ties break by capture
    order, keeping the sequence chronological where the arc can't classify."""
    arc = buckets(theme)
    text = (scene or "").lower()
    for index, (_name, keywords) in enumerate(arc):
        if any(keyword in text for keyword in keywords):
            return float(index)
    return len(arc) / 2.0
