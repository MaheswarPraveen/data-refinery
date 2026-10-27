-- The coordinator's memory. Every table answers one question.

-- Who is in the network?
CREATE TABLE IF NOT EXISTS nodes (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  secret      TEXT NOT NULL,             -- shared key used to sign every vote (HMAC)
  created_at  INTEGER NOT NULL,
  last_seen   INTEGER NOT NULL,
  quarantined INTEGER NOT NULL DEFAULT 0 -- 1 = failed too many honeypots, gets honeypots only
);

-- What needs refining? (gold is ONLY used for honeypots and final evaluation, never for consensus)
CREATE TABLE IF NOT EXISTS items (
  id           INTEGER PRIMARY KEY,
  question     TEXT NOT NULL,
  gold         TEXT NOT NULL,
  is_honeypot  INTEGER NOT NULL DEFAULT 0,
  status       TEXT NOT NULL DEFAULT 'pending', -- pending | accepted | disputed | dropped | done(honeypot)
  final_answer TEXT,
  score        REAL,                            -- 1.0 unanimous, 0.67 judge-confirmed
  resolved_by  TEXT                             -- unanimous | judge:<model> | no-majority | judge-rejected
);

-- Who is working on what right now? (a lease: expires if the node vanishes)
CREATE TABLE IF NOT EXISTS assignments (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id     INTEGER NOT NULL,
  node_id     TEXT NOT NULL,
  assigned_at INTEGER NOT NULL,
  same_node   INTEGER NOT NULL DEFAULT 0, -- 1 = fallback second vote from a node that already voted
  status      TEXT NOT NULL DEFAULT 'open' -- open | done
);

-- What did each node answer? This is the provenance log.
CREATE TABLE IF NOT EXISTS votes (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id       INTEGER NOT NULL,
  node_id       TEXT NOT NULL,
  assignment_id INTEGER NOT NULL,
  model         TEXT NOT NULL,
  harness       TEXT NOT NULL,
  answer        TEXT NOT NULL,   -- normalised number as text
  reasoning     TEXT,
  server_ms     INTEGER NOT NULL, -- measured by the coordinator, not reported by the node
  too_fast      INTEGER NOT NULL DEFAULT 0,
  honeypot_ok   INTEGER,          -- NULL for normal items, 1/0 for honeypots
  created_at    INTEGER NOT NULL
);

-- What did the judge say about disputed items?
CREATE TABLE IF NOT EXISTS judgements (
  item_id    INTEGER NOT NULL,
  model      TEXT NOT NULL,
  verdict    TEXT,
  raw        TEXT,
  created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_assign_item ON assignments(item_id);
CREATE INDEX IF NOT EXISTS idx_assign_node ON assignments(node_id);
CREATE INDEX IF NOT EXISTS idx_votes_item  ON votes(item_id);
CREATE INDEX IF NOT EXISTS idx_votes_node  ON votes(node_id);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
