-- What the library app deleted, and what it had to change because of it.
--
-- Recorded even for recycle-bin deletes, which Windows can already restore:
-- the file coming back is not the whole undo. A registered file's source.db
-- row was set to 'pending' when it went, and the projects that referenced it
-- were left pointing at a path that no longer resolves. Without this table
-- there is no record of which rows those were, so "I deleted the wrong thing"
-- has no answer beyond re-scanning and re-hashing.
--
-- Permanent deletes are not undoable by design. The row is still written, so
-- the question "what happened to this file" stays answerable afterwards.

CREATE TABLE library_delete (
  delete_id        INTEGER PRIMARY KEY,
  batch_id         TEXT NOT NULL,
  path             TEXT NOT NULL,          -- absolute, as it was at delete time
  size_bytes       INTEGER,
  content_hash     TEXT,                   -- re-identifies the file if restored
  media_id         INTEGER,                -- source.db row parked as 'pending'
  was_status       TEXT,                   -- registration verdict before deletion
  projects_affected INTEGER NOT NULL DEFAULT 0,
  permanent        INTEGER NOT NULL DEFAULT 0,  -- 0 = recycle bin, 1 = unrecoverable
  deleted_ms       INTEGER NOT NULL
);
CREATE INDEX idx_libdel_batch ON library_delete(batch_id, delete_id);
CREATE INDEX idx_libdel_hash ON library_delete(content_hash) WHERE content_hash IS NOT NULL;
