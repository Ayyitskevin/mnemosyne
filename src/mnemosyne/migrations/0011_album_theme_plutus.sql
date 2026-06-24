-- Gallery theme drives vision + arrange prompts (food, wedding, general, event).
-- Optional Plutus storefront offer URL for print cross-sell on share view.
ALTER TABLE albums ADD COLUMN gallery_theme TEXT NOT NULL DEFAULT 'food';
ALTER TABLE albums ADD COLUMN plutus_offer_url TEXT;