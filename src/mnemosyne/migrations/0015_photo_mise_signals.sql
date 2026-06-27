-- 0015_photo_mise_signals: let a photo carry Mise's identity and culling signals.
--
-- Mnemosyne is the ALBUMS worker for Mise Solo Studio OS: it should reference
-- Mise's asset ids and consume the per-photo scores Mise already stores, rather
-- than minting its own ids and recomputing vision. Two nullable columns make that
-- possible without disturbing the standalone (upload) path:
--
--   mise_asset_id — the asset's id in Mise's id space. NULL for upload albums and
--     legacy rows; the proposal reports COALESCE(mise_asset_id, id) so a placement
--     references Mise's id when known and falls back to the local id otherwise.
--   keeper_score  — Mise's per-photo keeper signal (0..1), the input a later
--     intentional-cull pass reads. NULL until a Mise import populates it.
--
-- (hero_score already exists from 0001 and now doubles as the home for Mise's
-- hero_potential when an import supplies it.) FORWARD-ONLY: never edit once
-- applied — add 0016_*.sql instead.

ALTER TABLE photos ADD COLUMN mise_asset_id INTEGER;
ALTER TABLE photos ADD COLUMN keeper_score REAL;

-- Two albums could in principle import the same Mise gallery (allow_duplicate),
-- so this is intentionally NOT unique — it indexes lookups, not identity.
CREATE INDEX idx_photos_mise_asset ON photos(mise_asset_id);
