# Retiring Mnemosyne

Mnemosyne is the **ALBUMS worker** for Mise Solo Studio OS. It is designed to be
**stateless and retire-ready**: it holds no authority and no irreplaceable data, so
it can be turned off — for one album or entirely — without losing anything Mise owns.
This document says exactly what is safe to turn off, and how to roll back.

The short version: **Mise is authoritative; Mnemosyne is a cache.** Turning Mnemosyne
off loses no source data and no approved decision — Mise falls back to its own
deterministic baseline proposer, and any Mnemosyne layout can be regenerated.

## What Mise owns (authoritative — Mnemosyne never owns these)

- **Galleries and original media.** The photographer's originals live in Mise. When
  Mnemosyne runs in reference mode it reads those originals in place and never copies
  them, so there is no second store of the media to clean up or keep in sync.
- **Per-photo signals.** `hero_potential` and `keeper_score` are computed and stored
  by Mise (/Argus). Mnemosyne *consumes* them; it does not recompute or re-own them.
- **The validator.** Mise's deterministic layout validator is the source of truth for
  whether a proposal is acceptable. Mnemosyne validates at the source as defense in
  depth, but Mise re-validates and its verdict wins.
- **Human approval and print/export.** A person approves every layout in Mise before
  anything prints or ships. Mnemosyne only ever emits a *proposal in review state*.

## What Mnemosyne owns (a derived cache — safe to drop)

- **The proposed layout** (`spreads` / `placements`) — a curated, ordered subset of a
  gallery's photos laid into spreads, emitted as the strict proposal JSON
  (`CONTRACT.md`, `docs/proposal.schema.json`). This is **derived and reproducible**:
  given the same gallery photos + signals + request, the deterministic engine
  (`MNEMOSYNE_ARRANGE_BACKEND=deterministic`) produces the same layout every time. It
  is a cache of a computation, not a system of record.
- **Operational state** for the standalone product mode only (accounts, billing,
  uploaded galleries). This exists for the SaaS deployment, not the Mise-worker role;
  in the worker role Mnemosyne reads Mise galleries and stores only the layout cache.

Nothing in this list is irreplaceable: the layout can be recomputed, and the
standalone product state is only relevant if you are running the standalone product.

## What is safe to turn off

- **The whole worker.** Stop the service (or the background `AlbumWorker`). Mise
  continues to function on its own baseline proposer; no gallery, asset, or signal is
  affected. In-flight album builds simply don't finish — re-running picks them up.
- **A single album's layout.** Delete or regenerate it (below). The gallery and its
  originals are untouched.
- **The cloud/grok inference lanes.** The deterministic engine and local Ollama lanes
  need no external API; turning off `XAI_API_KEY` only disables the optional billed
  lanes.

## Rollback

- **Regenerate one album's layout** — re-run the arrange step
  (`POST /albums/{id}/regenerate`, or `pipeline.regenerate_layout`). Vision scores and
  photo references are kept; only `spreads`/`placements` are replaced. With the
  deterministic backend the result is identical to the prior run.
- **Drop one album** — `pipeline.delete_album` removes the cached layout and the
  album's *own* stored bytes. In reference mode the Mise originals are **not** touched:
  referenced keys are absolute paths outside the upload root, and both the storage
  `delete_prefix` and the source-folder cleanup are containment-guarded to the upload
  root, so a referenced gallery on disk is never deleted.
- **Retire entirely** — stop the service and, if you want, delete the Mnemosyne SQLite
  cache file (`MNEMOSYNE_DB`) and the upload dir (`MNEMOSYNE_UPLOAD_DIR`). Mise's
  galleries, originals, and signals are in Mise and are unaffected. Re-pointing a fresh
  Mnemosyne at the same Mise reproduces the same proposals.

## Health & observability

`GET /healthz` reports liveness, the worker, the active vision/arrange backends, and
the storage probe — the single endpoint to check before and after turning things off.

## Statelessness invariants (what keeps this true)

- **No media duplication** in reference mode (`MNEMOSYNE_REFERENCE_MISE_ORIGINALS=true`,
  local storage + Mise imports): originals are read in place — the photo's storage_key
  is the original's path — never copied into a second store. `/healthz` reports
  `reference_mise_originals` so the posture is observable.
- **No second authority store**: the layout is a reproducible cache; Mise validates.
- **Reproducible outputs**: the deterministic engine yields a stable proposal per
  (gallery, request), so a rebuilt cache matches the original.
- **Mock-only, reproducible CI**: no test makes a live model or Mise call, so the
  conformance guarantees are checked deterministically (`tests/test_conformance.py`).
