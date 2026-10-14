-- Library management: registration state, and an undo log for executed moves.
--
-- channel.db already knows what is on disk. What it could not answer is the
-- question the library view is built around: "is this file in source.db yet?"
-- Answering that needs a real sha256, because source.db's identity key is the
-- content hash, not the path - the same shot moved to a new folder is still
-- registered, and two copies of one file are one registration.
--
-- The hash is cached here rather than recomputed, and stamped with the mtime
-- it was taken at so a re-encoded or replaced file is detected as stale
-- instead of silently keeping an identity that no longer describes its bytes.

ALTER TABLE file ADD COLUMN content_hash_mtime_ms INTEGER;

-- The verdict for one library file, as of checked_ms.
--
-- Cached rather than computed per request because the expensive half (hashing)
-- is what produces it, and because a stale verdict is still useful: it says
-- what was true at a known time, which is what lets the UI show the whole tree
-- immediately and re-check in the background.
CREATE TABLE library_registration (
  file_id      INTEGER PRIMARY KEY REFERENCES file(file_id) ON DELETE CASCADE,
  -- registered   - hash found in source.db, and source.db's path is correct
  -- path_stale   - hash found, but source.db points somewhere else (fixable)
  -- unregistered - hash not in source.db
  -- unhashed     - not yet checked
  status       TEXT NOT NULL,
  media_id     INTEGER,          -- source.db media_id, when matched
  source_path  TEXT,             -- media.file_path as source.db currently has it
  checked_ms   INTEGER NOT NULL
);
CREATE INDEX idx_libreg_status ON library_registration(status);

-- Every move this program executed, and what it had to update because of it.
--
-- A move is three writes that must agree: the bytes on disk, source.db's
-- media.file_path, and the FilePath entries of every .prproj that referenced
-- it. Recording them together is what makes the set undoable as one unit;
-- undoing only the file move would leave both databases pointing at a file
-- that is no longer there.
CREATE TABLE library_move (
  move_id          INTEGER PRIMARY KEY,
  batch_id         TEXT NOT NULL,
  old_path         TEXT NOT NULL,
  new_path         TEXT NOT NULL,
  media_id         INTEGER,                    -- source.db row repointed, if any
  projects_updated INTEGER NOT NULL DEFAULT 0,
  project_backups  TEXT,                       -- JSON list of {project, backup}
  applied_ms       INTEGER NOT NULL,
  undone_ms        INTEGER
);
CREATE INDEX idx_libmove_batch ON library_move(batch_id, move_id);
CREATE INDEX idx_libmove_open ON library_move(undone_ms) WHERE undone_ms IS NULL;
