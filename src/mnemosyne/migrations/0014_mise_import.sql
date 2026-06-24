-- Mise gallery import metadata + Plutus run id for auto-link on album ready.
ALTER TABLE albums ADD COLUMN mise_gallery_id INTEGER;
ALTER TABLE albums ADD COLUMN plutus_run_id INTEGER;