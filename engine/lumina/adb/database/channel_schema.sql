-- Read-only inventory of the YouTube channel folder.
--
-- Deliberately a separate database from source.db. source.db is a curated
-- catalogue of shots chosen for editing; this is a raw census of whatever is
-- on disk, most of which is cache, autosaves and duplicates. Mixing them
-- would put junk into a searched index, the same reason ingest.db exists.
--
-- Nothing here is authoritative about intent. It records what a scan saw, so
-- that later questions - what is duplicated, what is orphaned, what a project
-- needs - are answered by query rather than by walking 19,000 files again.

CREATE TABLE scan (
  scan_id      INTEGER PRIMARY KEY,
  root         TEXT NOT NULL,
  started_ms   INTEGER NOT NULL,
  finished_ms  INTEGER,
  file_count   INTEGER,
  total_bytes  INTEGER
);

CREATE TABLE file (
  file_id     INTEGER PRIMARY KEY,
  rel_path    TEXT NOT NULL UNIQUE,          -- relative to scan root, '\' separated
  name        TEXT NOT NULL,
  ext         TEXT NOT NULL,                 -- lowercased, includes the dot; '' if none
  size_bytes  INTEGER NOT NULL,
  mtime_ms    INTEGER NOT NULL,
  depth       INTEGER NOT NULL,              -- path segments below the root
  kind        TEXT NOT NULL,                 -- see channel_inventory.classify
  -- size + head + tail digest. A full hash of 171GB is hours; this is two
  -- seeks per file and is only ever used to *propose* duplicates. Equal
  -- quick_hash means "worth hashing fully", never "identical".
  quick_hash  TEXT,
  content_hash TEXT,                         -- filled only where it was needed
  -- The mtime the content_hash was taken at. A file re-encoded or replaced in
  -- place keeps its path, so without this the cached digest would keep
  -- asserting an identity that no longer matches the bytes (migration_006).
  content_hash_mtime_ms INTEGER
);
CREATE INDEX idx_file_kind ON file(kind);
CREATE INDEX idx_file_quick ON file(quick_hash) WHERE quick_hash IS NOT NULL;
CREATE INDEX idx_file_size ON file(size_bytes);

-- What a .prproj points at.
--
-- ref_kind matters more than it looks. Premiere stores three different kinds
-- of path in a project and only one is a real dependency:
--   media   - FilePath / ActualMediaFilePath, the actual footage
--   history - MediaFileHistory*, where the file used to live. This is the
--             field a relink matches on (by old path, not by node id), so it
--             is the migration's most useful column rather than dead weight.
--   cache   - ConformedAudioPath / PeakFilePath, which live in %APPDATA% and
--             regenerate. Counting these as dependencies would report every
--             project as broken.
CREATE TABLE project_ref (
  ref_id           INTEGER PRIMARY KEY,
  project_file_id  INTEGER NOT NULL REFERENCES file(file_id) ON DELETE CASCADE,
  ref_kind         TEXT NOT NULL,            -- media | history | cache
  raw_path         TEXT NOT NULL,            -- as stored, extended prefix stripped
  resolved_file_id INTEGER REFERENCES file(file_id),  -- NULL when outside the root or gone
  is_inside_root   INTEGER NOT NULL DEFAULT 0,
  exists_on_disk   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_ref_project ON project_ref(project_file_id);
CREATE INDEX idx_ref_resolved ON project_ref(resolved_file_id);
CREATE INDEX idx_ref_kind ON project_ref(ref_kind);

-- Projects that could not be read at all, kept rather than dropped: a project
-- this scan could not parse is exactly the one a migration must not move
-- blindly.
CREATE TABLE project_error (
  project_file_id INTEGER PRIMARY KEY REFERENCES file(file_id) ON DELETE CASCADE,
  message         TEXT NOT NULL
);

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
