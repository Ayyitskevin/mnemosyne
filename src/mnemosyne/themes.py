"""Gallery themes — vision and arrange prompts per shoot type."""
from __future__ import annotations

THEMES = ("food", "wedding", "general", "event")

_ARRANGE_RULES = (
    "Rules: each spread holds 1 to 4 photos, and MOST spreads should hold 3 or 4 — "
    "a printed album breathes through full, varied spreads, not a slideshow of "
    "half-empty pages. Reserve a 1-photo spread ONLY for a single standout hero "
    "(the very highest hero_score), and never use more than one or two such solo "
    "spreads in the whole album. Do NOT crowd two high hero_score photos onto one "
    "spread — give each its own spread as the clear hero. Mix orientations so a "
    "spread looks balanced; use EVERY photo exactly once. "
    'Respond ONLY as JSON: {"spreads": [{"photos": [id, ...], "hero": id}, ...]} '
    "in album order."
)

_ARRANGE_SYSTEM: dict[str, str] = {
    "food": (
        "You are an album designer laying out a photo album from a restaurant photo "
        "shoot. You will get a list of photos as (id, scene, hero_score, orientation). "
        "Design the album as an ordered list of two-page SPREADS that tells the story "
        "of the shoot: arrival/exterior -> ambiance -> details & drinks -> hero dishes "
        "-> action -> dessert -> closing. "
    ),
    "wedding": (
        "You are an album designer laying out a wedding photo album. You will get a "
        "list of photos as (id, scene, hero_score, orientation). Design the album as "
        "an ordered list of two-page SPREADS that tells the wedding story: getting "
        "ready -> details (rings, dress, flowers) -> ceremony -> couple portraits -> "
        "family & wedding party -> reception entrance -> toasts & candids -> "
        "dancing & celebration -> send-off or closing ambiance. "
    ),
    "general": (
        "You are an album designer laying out a portrait or lifestyle photo album. "
        "You will get a list of photos as (id, scene, hero_score, orientation). "
        "Design the album as an ordered list of two-page SPREADS with a natural arc: "
        "establishing context -> subjects & relationships -> detail shots -> "
        "environment & atmosphere -> strongest hero moments -> closing. "
    ),
    "event": (
        "You are an album designer laying out a corporate or social event album. "
        "You will get a list of photos as (id, scene, hero_score, orientation). "
        "Design the album as an ordered list of two-page SPREADS: venue & arrival -> "
        "keynote or main activity -> candid networking -> detail & branding -> "
        "group moments -> highlights & energy -> closing. "
    ),
}

_VISION_SCENES: dict[str, str] = {
    "food": (
        "e.g. 'wide interior establishing shot', 'overhead hero plated dish', "
        "'macro food detail', 'chef plating action', 'cocktail/drink detail', "
        "'closing ambiance shot'"
    ),
    "wedding": (
        "e.g. 'getting ready detail', 'wide ceremony establishing shot', "
        "'couple portrait', 'ring or bouquet detail', 'first dance', "
        "'family group', 'reception candids', 'send-off moment'"
    ),
    "general": (
        "e.g. 'environment establishing shot', 'subject portrait', "
        "'candid interaction', 'detail close-up', 'wide context shot', "
        "'closing mood shot'"
    ),
    "event": (
        "e.g. 'venue wide shot', 'speaker or stage moment', 'audience reaction', "
        "'networking candids', 'branding detail', 'group photo', 'closing shot'"
    ),
}

_VISION_SUBJECT: dict[str, str] = {
    "food": "a restaurant/food photo shoot",
    "wedding": "a wedding photo shoot",
    "general": "a portrait or lifestyle photo shoot",
    "event": "a corporate or social event photo shoot",
}


def normalize_theme(theme: str | None) -> str:
    key = (theme or "food").strip().lower()
    return key if key in THEMES else "food"


def arrange_system(theme: str | None) -> str:
    key = normalize_theme(theme)
    return _ARRANGE_SYSTEM[key] + _ARRANGE_RULES


def vision_prompt(theme: str | None) -> str:
    key = normalize_theme(theme)
    scenes = _VISION_SCENES[key]
    subject = _VISION_SUBJECT[key]
    return (
        f"You are a photo editor culling {subject} to design an album. "
        "Look at this single photo and respond ONLY with JSON of the form "
        '{"scene": "...", "hero_score": 0.0}. '
        "scene = a short 3-6 word label of what the shot is and its role in the "
        f"story, {scenes}. "
        "hero_score = a float 0.0-1.0 for how striking and album-cover-worthy this ONE "
        "image is on its own. BE DISCERNING AND USE THE FULL RANGE — in any real "
        "gallery MOST shots are NOT heroes, so most scores should fall BELOW 0.6. "
        "Reserve high scores; do not bunch everything near 0.8. Calibrate to this "
        "rubric: "
        "0.9-1.0 = exceptional, genuinely cover-worthy; "
        "0.7-0.85 = strong feature shot, would anchor a spread but not the cover; "
        "0.4-0.65 = solid supporting shot; "
        "0.2-0.35 = a small detail or context filler; "
        "0.0-0.15 = weak or throwaway. "
        "Judge THIS photo honestly against that scale."
    )