-- Votes per (node, model): lets the dashboard name each node by the Llamas it actually ran
-- without scanning the votes table on every refresh.
CREATE TABLE IF NOT EXISTS node_models (
  node_id TEXT NOT NULL,
  model   TEXT NOT NULL,
  votes   INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (node_id, model)
);
