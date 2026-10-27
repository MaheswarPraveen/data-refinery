-- The job picker asks "has this node voted on this item?" and "how many live leases does this item have?"
-- for every candidate item. Without these, SQLite used the per-node / per-status indexes and read
-- every vote of the node (or every open lease) per candidate: ~10-27k rows per job, over the 5M/day budget.
CREATE INDEX IF NOT EXISTS idx_votes_item_node   ON votes(item_id, node_id);
CREATE INDEX IF NOT EXISTS idx_assign_item_open  ON assignments(item_id, status, assigned_at);
CREATE INDEX IF NOT EXISTS idx_items_status_id   ON items(status, id);
