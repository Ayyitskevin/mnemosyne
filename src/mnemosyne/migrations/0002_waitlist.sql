-- 0002_waitlist: the demand-validation signup list. Before mnemosyne grows a
-- SaaS spine (accounts, cloud inference, billing), we want one cheap signal —
-- do strangers actually want this? A single table behind a public landing page
-- answers that with zero infrastructure. FORWARD-ONLY: never edit once applied,
-- add 0003_*.sql instead.

-- One row per interested person. email is stored already normalized (trimmed +
-- lowercased by the code that inserts it) so the UNIQUE constraint does the
-- deduping for us — a second signup with the same address is a no-op, not a
-- duplicate row. source records WHERE the signup came from (e.g. "landing") so a
-- later campaign can tell channels apart; nullable because the earliest signups
-- predate any campaign tagging.
CREATE TABLE waitlist (
    id          INTEGER PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,
    source      TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
