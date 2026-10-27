-- Running totals, so the dashboard reads a few rows instead of re-counting every vote.
-- (D1 free tier: 5M rows read per day. Re-counting thousands of votes every 15 s would burn it.)
-- Run once on a database created from the original schema.sql.

ALTER TABLE nodes ADD COLUMN votes_n    INTEGER NOT NULL DEFAULT 0;
ALTER TABLE nodes ADD COLUMN hp_n       INTEGER NOT NULL DEFAULT 0; -- honeypots answered
ALTER TABLE nodes ADD COLUMN hp_ok      INTEGER NOT NULL DEFAULT 0; -- honeypots answered correctly
ALTER TABLE nodes ADD COLUMN too_fast_n INTEGER NOT NULL DEFAULT 0;
ALTER TABLE nodes ADD COLUMN agreed_n   INTEGER NOT NULL DEFAULT 0; -- votes that matched an accepted answer

CREATE TABLE IF NOT EXISTS pair_stats (
  model   TEXT NOT NULL,
  harness TEXT NOT NULL,
  votes   INTEGER NOT NULL DEFAULT 0,
  correct INTEGER NOT NULL DEFAULT 0,   -- matched the official answer (measurement only)
  PRIMARY KEY (model, harness)
);

CREATE INDEX IF NOT EXISTS idx_votes_time  ON votes(created_at);
CREATE INDEX IF NOT EXISTS idx_assign_open ON assignments(status, assigned_at);
CREATE INDEX IF NOT EXISTS idx_assign_same ON assignments(same_node);
