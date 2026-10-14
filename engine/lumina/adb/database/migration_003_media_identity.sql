-- Migration 003: media identity, availability, and path kind.
--
-- Three problems this closes, all of which had been handled by convention
-- (or not at all) and were therefore invisible to any query:
--
--   1. The same picture saved twice in different formats (a.png / a.webp)
--      has two different file hashes, so get_or_create_media's hash dedupe
--      cannot see the duplication and search returns the same image twice.
--      canonical_media_id mirrors tag.canonical_tag_id: the duplicate keeps
--      its row (the file is real and still on disk) but points at the
--      representative, and the search layer skips non-canonical rows.
--
--   2. Rows describing a source that has been planned but not yet acquired
--      were being encoded as a "PENDING://<description>" pseudo-path. They
--      matched searches and would then fail at placement time. availability
--      makes that state explicit and filterable instead of a path convention.
--
--   3. One media row deliberately points at a directory (a bundled set of
--      rig assets registered as a single entry rather than hundreds of rows).
--      Anything that opens media.file_path as a file breaks on it. path_kind
--      declares that up front.

ALTER TABLE media ADD COLUMN canonical_media_id INTEGER REFERENCES media(media_id);

ALTER TABLE media ADD COLUMN availability TEXT NOT NULL DEFAULT 'available'
  CHECK(availability IN ('available', 'pending'));

ALTER TABLE media ADD COLUMN path_kind TEXT NOT NULL DEFAULT 'file'
  CHECK(path_kind IN ('file', 'directory'));

CREATE INDEX IF NOT EXISTS idx_media_canonical ON media(canonical_media_id);
CREATE INDEX IF NOT EXISTS idx_media_availability ON media(availability);
