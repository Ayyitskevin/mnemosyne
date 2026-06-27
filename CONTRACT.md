# Mnemosyne Worker Contract

Mnemosyne is the **ALBUMS worker** for Mise Solo Studio OS. Its job is to propose a
curated, ordered subset of a gallery's photos laid into spreads. It is a
**stateless, contract-true proposer**: Mise's deterministic validator is
authoritative, and a human approves every layout before any print/export.

This document defines the proposal Mnemosyne emits and the rules Mise re-checks.
The machine-readable schema is [`docs/proposal.schema.json`](docs/proposal.schema.json);
the reference validator and serializer live in
[`src/mnemosyne/proposal.py`](src/mnemosyne/proposal.py).

## The correctness guardrail

A layout must **never silently omit, duplicate, or misassign** a photo. Mise
re-validates every proposal and **rejects** a malformed one, so Mnemosyne conforms
exactly. Omitting an eligible photo is allowed (a cull), but it must be
**intentional and surfaced** — Mise shows omissions, it never hides them.

## The proposal

A proposal is strict JSON:

```json
{
  "placements": [
    {"asset_id": 1421, "spread": 0, "slot": 0},
    {"asset_id": 1408, "spread": 0, "slot": 1},
    {"asset_id": 1455, "spread": 1, "slot": 0}
  ],
  "provider": "ollama",
  "model": "qwen3.6:35b",
  "notes": "optional"
}
```

### Rules a valid proposal satisfies

1. `placements` is a list; `provider` and `model` are non-empty strings; `notes` is
   an optional string.
2. Every placement is an object with an integer `asset_id` and integer `spread` and
   `slot`, both `>= 0` (0-based).
3. `asset_id` references a photo that **belongs to this gallery and is
   processed/ready** (eligible).
4. Each `asset_id` appears **at most once** across the proposal.
5. Each `(spread, slot)` pair appears **at most once** across the proposal.

`validate_proposal(proposal, eligible_asset_ids)` mirrors exactly these rules.
`build_proposal(conn, album_id)` serializes Mnemosyne's committed layout into this
shape — densifying spreads to `0..k-1` and each spread's slots to `0..m-1` so the
indices are contiguous and collision-free even after manual nudges.

## How a proposal references Mise's gallery + assets

- **Gallery** — the album already records its source gallery as
  `albums.mise_gallery_id`; that scopes the proposal.
- **Asset id** — `asset_id` is the gallery's asset id. A Mise-imported photo carries
  Mise's id in `photos.mise_asset_id`. The proposal uses **one id space per album**:
  Mise's ids when *every* photo in the album is Mise-mapped, otherwise the local
  `photos.id` for the whole album (upload albums, legacy rows, or a partially-matched
  import). Never a per-row fallback — a per-row mix could let one photo's local id
  equal another's Mise id and read as a duplicate placement. The
  `_use_mise_ids` / `_asset_id_col` chokepoint in `proposal.py` is the single place
  this decision lives.
- **Signals** — for a Mise import, `mise_import.apply_mise_signals` reads the
  gallery's per-photo `hero_potential` / `keeper_score` that Mise already stores
  (`mise_client.list_assets`) and stamps them onto the photo rows before the look
  step. Mise's scene + hero are adopted (and the look step then skips the photo, so
  the score is consumed not recomputed) **only when the asset is `processed` and the
  signal is complete** — a scene label **and** a hero_potential. An unprocessed or
  partial asset keeps its Mise id but is scored locally, so a provisional score is
  never trusted as final and a build never fails for want of a signal. Ambiguous
  duplicate filenames in Mise's asset list are skipped (scored locally) rather than
  risk a misassignment. The per-asset endpoint is `MNEMOSYNE_MISE_ASSETS_PATH` (the
  one knob to retarget if Mise's route/field names differ); the client is tolerant of
  the response shape.

## Provenance & cost

`provider` and `model` identify the proposer. Latency and cost (`latency_ms`,
`cost_usd`, with `0` for local/free inference) are reserved fields in the schema and
populated by the provenance pass.

## Idempotency

A proposal is **stable per (gallery, request)**: the deterministic engine produces the
same layout for the same inputs, and the proposal is cached under a request
fingerprint (theme + arrange backend + each eligible photo's signals). A retry
returns the byte-identical cached proposal instead of recomputing; re-arranging the
album (`regenerate`) invalidates the cache so the next request reflects the new
layout. The cache is derived state, never a second store of authority — it is
rebuilt from the layout, and dropped when the album is.

## Endpoint

`GET /albums/{album_id}/proposal.json` (owner-gated) returns the proposal for an
album the caller owns. It is read-only and does not mutate the album, and is
idempotent — repeated calls return the same cached proposal.
